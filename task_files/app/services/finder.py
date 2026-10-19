"""Find new people at a company by job title and add them to HubSpot."""

from dataclasses import dataclass

import structlog

from app.clients.forager import ForagerClient, ForagerError, role_search_payload, title_query
from app.clients.hubspot import HubSpotClient
from app.config import Settings
from app.credits import CREDIT_COST, CreditBudgetExceededError
from app.mapping import contact_properties, now_ms
from app.models import Organization, Person, PersonRole
from app.properties import FINDER_SOURCE
from app.services.lookups import lookup_phone, lookup_work_email, org_domain
from app.services.results import CandidateView, CreatedContact, FinderResult, SkippedCandidate

log = structlog.get_logger(__name__)

MAX_PAGES = 2


@dataclass
class Candidate:
    role: PersonRole
    person: Person
    organization: Organization | None

    def view(self) -> CandidateView:
        li = self.person.linkedin_info
        return CandidateView(
            forager_person_id=self.person.id,
            name=self.person.full_name,
            title=self.role.role_title,
            linkedin_url=li.public_profile_url if li else None,
            location=self.person.location.name if self.person.location else None,
        )


def interleave_by_title(candidates: list[Candidate], titles: list[str]) -> list[Candidate]:
    """Order candidates round-robin across the requested titles, so a small limit still
    returns a spread of roles instead of five people with the same title."""
    buckets: dict[str, list[Candidate]] = {t.lower(): [] for t in titles}
    other: list[Candidate] = []
    for candidate in candidates:
        role_title = (candidate.role.role_title or "").lower()
        bucket = next((b for t, b in buckets.items() if t in role_title), other)
        bucket.append(candidate)
    queues = [*buckets.values(), other]
    ordered: list[Candidate] = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


class ContactFinder:
    def __init__(self, hubspot: HubSpotClient, forager: ForagerClient, settings: Settings) -> None:
        self._hubspot = hubspot
        self._forager = forager
        self._settings = settings

    async def search(
        self,
        *,
        titles: list[str],
        domain: str | None,
        org_id: int | None,
        page: int = 1,
    ) -> list[Candidate]:
        """One role search (1 credit): current employees whose title matches any of `titles`."""
        roles = await self._forager.person_role_search(
            role_search_payload(
                organization_ids=[org_id] if org_id else None,
                organization_domains=[domain] if domain and not org_id else None,
                role_title=title_query(titles),
                page=page,
            )
        )
        seen: set[int | None] = set()
        candidates: list[Candidate] = []
        for role in roles:
            if role.person is None or role.person.id in seen:
                continue
            seen.add(role.person.id)
            candidates.append(Candidate(role, role.person, role.organization))
        return interleave_by_title(candidates, titles)

    async def find(
        self,
        *,
        company_id: str,
        titles: list[str],
        limit: int,
        domain: str | None,
        org_id: int | None,
        preview: bool = False,
    ) -> FinderResult:
        """Find up to `limit` people with a work email and create them in HubSpot.

        With `preview`, only the search runs (1 credit) and nothing is written. People whose
        email lookup finds nothing are skipped; Forager does not charge for those.
        """
        result = FinderResult(company_id=company_id, titles=titles, preview=preview)
        if not titles:
            result.stopped_reason = "no job titles given"
            return result
        for page in range(1, MAX_PAGES + 1):
            try:
                candidates = await self.search(
                    titles=titles, domain=domain, org_id=org_id, page=page
                )
            except (CreditBudgetExceededError, ForagerError) as exc:
                result.stopped_reason = str(exc)
                break
            if not candidates:
                break
            result.candidates.extend(c.view() for c in candidates)
            if preview:
                break
            for candidate in candidates:
                if len(result.created) >= limit or result.stopped_reason:
                    break
                try:
                    await self._add(candidate, company_id, domain, result)
                except ForagerError as exc:
                    # Stop instead of paying for more lookups that would fail the same way.
                    log.error("contact_finder_stopped", error=str(exc))
                    result.stopped_reason = str(exc)
            if len(result.created) >= limit or result.stopped_reason:
                break
        log.info(
            "contact_finder_done",
            company_id=company_id,
            titles=titles,
            preview=preview,
            candidates=len(result.candidates),
            created=len(result.created),
            skipped=len(result.skipped),
            stopped_reason=result.stopped_reason,
        )
        return result

    async def _add(
        self, candidate: Candidate, company_id: str, domain: str | None, result: FinderResult
    ) -> None:
        person = candidate.person
        name = person.full_name
        linkedin_url = person.linkedin_info.public_profile_url if person.linkedin_info else None
        if linkedin_url:
            existing = await self._hubspot.search_contacts("linkedin_url", linkedin_url, ["email"])
            if existing:
                result.skipped.append(SkippedCandidate(name=name, reason="already in HubSpot"))
                return

        if self._forager_budget_exhausted():
            result.stopped_reason = "credit budget exhausted"
            return
        email = await lookup_work_email(
            self._forager, person, org_domain(candidate.organization) or domain
        )
        if email is None:
            result.skipped.append(SkippedCandidate(name=name, reason="no work email found"))
            return
        phone = await lookup_phone(
            self._forager, person, enabled=self._settings.enable_phone_lookup
        )

        props = contact_properties(
            person, candidate.role, candidate.organization, email=email, phone=phone
        )
        props["enrichment_source"] = FINDER_SOURCE
        props["forager_enriched_at"] = now_ms()
        props["forager_enrichment_status"] = "created by contact finder"
        contact_id, created = await self._hubspot.create_contact(props)
        await self._hubspot.associate_default("contacts", contact_id, "companies", company_id)
        result.created.append(
            CreatedContact(
                contact_id=contact_id,
                name=name,
                title=candidate.role.role_title,
                email=email,
                created=created,
            )
        )
        log.info("contact_created", contact_id=contact_id, title=candidate.role.role_title)

    def _forager_budget_exhausted(self) -> bool:
        return self._forager.credits_remaining < CREDIT_COST["work_email"]
