"""Enrich a HubSpot company, then enrich its contacts or find new ones."""

from typing import Any

import structlog

from app.clients.forager import ForagerClient, ForagerError
from app.clients.hubspot import HubSpotClient, HubSpotError
from app.config import Settings
from app.credits import CreditBudgetExceededError
from app.mapping import (
    company_properties,
    linkedin_slug,
    merge_properties,
    normalize_domain,
    now_ms,
)
from app.models import Organization
from app.properties import COMPANY_READ
from app.services.contact import CompanyContext, ContactEnricher
from app.services.finder import ContactFinder
from app.services.results import CompanyResult

log = structlog.get_logger(__name__)


def pick_organization(orgs: list[Organization], domain: str | None) -> Organization | None:
    if domain:
        for org in orgs:
            if normalize_domain(org.domain or org.website) == domain:
                return org
    return orgs[0] if orgs else None


class CompanyEnricher:
    def __init__(
        self,
        hubspot: HubSpotClient,
        forager: ForagerClient,
        contacts: ContactEnricher,
        finder: ContactFinder,
        settings: Settings,
    ) -> None:
        self._hubspot = hubspot
        self._forager = forager
        self._contacts = contacts
        self._finder = finder
        self._settings = settings
        self._industry_options: dict[str, str] | None = None

    async def enrich(
        self, company_id: str, *, force: bool = False, titles: list[str] | None = None
    ) -> CompanyResult:
        company = await self._hubspot.get_object("companies", company_id, COMPANY_READ)
        if company is None:
            return CompanyResult(
                company_id=company_id, status="not_found", detail="no such company"
            )
        props: dict[str, Any] = company.get("properties") or {}
        if not force and props.get("forager_enriched_at"):
            return CompanyResult(company_id=company_id, status="skipped", detail="already enriched")

        domain = normalize_domain(props.get("domain") or props.get("website"))
        slug = linkedin_slug(props.get("linkedin_company_page"))
        if not domain and not slug:
            await self._set_status(company_id, "skipped: no domain or LinkedIn page")
            return CompanyResult(
                company_id=company_id,
                status="skipped",
                detail="company has no domain or LinkedIn page to match on",
            )

        try:
            orgs = await self._forager.organization_search(
                domains=[domain] if domain else None,
                linkedin_ids=[slug] if slug and not domain else None,
            )
        except (ForagerError, CreditBudgetExceededError) as exc:
            log.warning("company_enrichment_failed", company_id=company_id, error=str(exc))
            await self._set_status(company_id, f"error: {exc}"[:250])
            return CompanyResult(company_id=company_id, status="error", detail=str(exc))

        org = pick_organization(orgs, domain)
        if org is None:
            await self._set_status(company_id, "not_found", mark_enriched=True)
            return CompanyResult(
                company_id=company_id, status="not_found", detail="no Forager match"
            )

        new = company_properties(org, await self._industries())
        new["forager_enriched_at"] = now_ms()
        new["forager_enrichment_status"] = "enriched"
        changes = merge_properties(props, new, overwrite=self._settings.enrichment_overwrite)
        await self._hubspot.update_object("companies", company_id, changes)
        log.info("company_enriched", company_id=company_id, updated=sorted(changes))
        result = CompanyResult(company_id=company_id, status="enriched", updated=sorted(changes))

        domain = domain or normalize_domain(org.domain or org.website)
        await self._handle_contacts(
            company_id, CompanyContext(company_id, domain, org), result, titles
        )
        return result

    async def _handle_contacts(
        self,
        company_id: str,
        context: CompanyContext,
        result: CompanyResult,
        titles: list[str] | None,
    ) -> None:
        """Enrich the company's existing contacts, or find new ones if it has none."""
        limit = self._settings.max_contacts_per_company
        contact_ids = await self._hubspot.associated_ids("companies", company_id, "contacts")
        if contact_ids:
            if len(contact_ids) > limit:
                log.info(
                    "contacts_capped", company_id=company_id, total=len(contact_ids), limit=limit
                )
            for contact_id in contact_ids[:limit]:
                result.contacts.append(await self._contacts.enrich(contact_id, context=context))
            return
        org_id = context.organization.id if context.organization else None
        result.finder = await self._finder.find(
            company_id=company_id,
            titles=titles or self._settings.job_titles,
            limit=limit,
            domain=context.domain,
            org_id=org_id,
        )

    async def _industries(self) -> dict[str, str]:
        """HubSpot's `industry` dropdown options, fetched once."""
        if self._industry_options is None:
            try:
                self._industry_options = await self._hubspot.enum_options("companies", "industry")
            except HubSpotError as exc:
                log.warning("industry_options_unavailable", error=str(exc))
                self._industry_options = {}
        return self._industry_options

    async def _set_status(
        self, company_id: str, status: str, *, mark_enriched: bool = False
    ) -> None:
        props = {"forager_enrichment_status": status}
        if mark_enriched:
            props["forager_enriched_at"] = now_ms()
        await self._hubspot.update_object("companies", company_id, props)
