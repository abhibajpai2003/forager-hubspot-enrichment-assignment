"""Enrich one existing HubSpot contact from Forager."""

from dataclasses import dataclass
from typing import Any

import structlog

from app.clients.forager import ForagerClient, ForagerError, role_search_payload
from app.clients.hubspot import HubSpotClient
from app.config import Settings
from app.credits import CreditBudgetExceededError
from app.mapping import (
    contact_properties,
    email_domain,
    linkedin_slug,
    merge_properties,
    normalize_domain,
    now_ms,
)
from app.models import Organization, Person, PersonRole
from app.properties import CONTACT_READ, FINDER_SOURCE
from app.services.lookups import lookup_phone, lookup_work_email, org_domain
from app.services.results import ContactResult

log = structlog.get_logger(__name__)


@dataclass
class CompanyContext:
    """What we know about the contact's employer before matching."""

    company_id: str | None
    domain: str | None
    organization: Organization | None = None


@dataclass
class Match:
    person: Person
    role: PersonRole | None
    organization: Organization | None
    method: str


def _same_name(person: Person, first: str, last: str) -> bool:
    return (person.first_name or "").casefold() == first.casefold() and (
        person.last_name or ""
    ).casefold() == last.casefold()


def _pick_role(roles: list[PersonRole], domain: str | None) -> PersonRole | None:
    """Prefer the role at the expected company, then the most recent one."""
    roles = [r for r in roles if r.person is not None]
    if not roles:
        return None
    if domain:
        at_company = [r for r in roles if org_domain(r.organization) == domain]
        if at_company:
            roles = at_company
    return max(roles, key=lambda r: r.start_date or "")


class ContactEnricher:
    def __init__(self, hubspot: HubSpotClient, forager: ForagerClient, settings: Settings) -> None:
        self._hubspot = hubspot
        self._forager = forager
        self._settings = settings

    async def enrich(
        self, contact_id: str, *, force: bool = False, context: CompanyContext | None = None
    ) -> ContactResult:
        contact = await self._hubspot.get_object("contacts", contact_id, CONTACT_READ)
        if contact is None:
            return ContactResult(
                contact_id=contact_id, status="not_found", detail="no such contact"
            )
        props: dict[str, Any] = contact.get("properties") or {}

        if not force and props.get("enrichment_source") == FINDER_SOURCE:
            return ContactResult(
                contact_id=contact_id,
                status="skipped",
                detail="created by the contact finder, already enriched",
            )
        if not force and props.get("forager_enriched_at"):
            return ContactResult(contact_id=contact_id, status="skipped", detail="already enriched")

        if context is None:
            context = await self._company_context(contact_id, props)

        try:
            match = await self._match(props, context)
            if match is None:
                await self._hubspot.update_object(
                    "contacts",
                    contact_id,
                    {"forager_enrichment_status": "not_found", "forager_enriched_at": now_ms()},
                )
                return ContactResult(
                    contact_id=contact_id, status="not_found", detail="no Forager match"
                )
            email = props.get("email") or await lookup_work_email(
                self._forager, match.person, org_domain(match.organization) or context.domain
            )
            phone = None
            if not props.get("phone"):
                phone = await lookup_phone(
                    self._forager, match.person, enabled=self._settings.enable_phone_lookup
                )
        except (ForagerError, CreditBudgetExceededError) as exc:
            log.warning("contact_enrichment_failed", contact_id=contact_id, error=str(exc))
            await self._hubspot.update_object(
                "contacts", contact_id, {"forager_enrichment_status": f"error: {exc}"[:250]}
            )
            return ContactResult(contact_id=contact_id, status="error", detail=str(exc))

        new = contact_properties(
            match.person, match.role, match.organization, email=email, phone=phone
        )
        new["forager_enriched_at"] = now_ms()
        new["forager_enrichment_status"] = f"enriched (matched by {match.method})"
        changes = merge_properties(props, new, overwrite=self._settings.enrichment_overwrite)
        await self._hubspot.update_object("contacts", contact_id, changes)
        log.info(
            "contact_enriched",
            contact_id=contact_id,
            method=match.method,
            updated=sorted(changes),
        )
        return ContactResult(
            contact_id=contact_id,
            status="enriched",
            match_method=match.method,
            updated=sorted(changes),
        )

    async def _company_context(self, contact_id: str, props: dict[str, Any]) -> CompanyContext:
        company_ids = await self._hubspot.associated_ids("contacts", contact_id, "companies")
        domain = None
        if company_ids:
            company = await self._hubspot.get_object(
                "companies", company_ids[0], ["domain", "website"]
            )
            company_props = (company or {}).get("properties") or {}
            domain = normalize_domain(company_props.get("domain") or company_props.get("website"))
        domain = (
            domain or email_domain(props.get("email")) or normalize_domain(props.get("website"))
        )
        return CompanyContext(company_id=company_ids[0] if company_ids else None, domain=domain)

    async def _match(self, props: dict[str, Any], context: CompanyContext) -> Match | None:
        """Find the contact in Forager, cheapest method first.

        1. LinkedIn URL -> role search (1 credit)
        2. Name + company -> role search (1 credit), only if exactly one person matches
        3. Email -> reverse lookup (5 credits)
        """
        slug = linkedin_slug(props.get("linkedin_url"))
        if slug:
            roles = await self._forager.person_role_search(
                role_search_payload(person_linkedin_ids=[slug])
            )
            role = _pick_role(roles, context.domain)
            if role and role.person:
                return Match(role.person, role, role.organization, "linkedin")

        first, last = props.get("firstname"), props.get("lastname")
        if first and last and (context.domain or context.organization):
            org_id = context.organization.id if context.organization else None
            roles = await self._forager.person_role_search(
                role_search_payload(
                    person_name=f'"{first} {last}"',
                    organization_ids=[org_id] if org_id else None,
                    organization_domains=None if org_id else [str(context.domain)],
                )
            )
            roles = [r for r in roles if r.person and _same_name(r.person, first, last)]
            people = {r.person.id for r in roles if r.person}
            if len(people) == 1:
                role = _pick_role(roles, context.domain)
                if role and role.person:
                    return Match(role.person, role, role.organization, "name_and_company")
            elif len(people) > 1:
                log.info("contact_match_ambiguous", candidates=len(people))

        email = props.get("email")
        if email:
            detail = await self._forager.reverse_lookup_by_email(email)
            if detail:
                current = detail.current_role()
                org = current.organization if current else None
                return Match(detail, current, org, "email")
        return None
