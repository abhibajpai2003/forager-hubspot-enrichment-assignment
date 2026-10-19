"""Paid email and phone lookups, shared by contact enrichment and the contact finder."""

import structlog

from app.clients.forager import ForagerClient
from app.credits import CreditBudgetExceededError
from app.mapping import normalize_domain
from app.models import Organization, Person

log = structlog.get_logger(__name__)


def org_domain(org: Organization | None) -> str | None:
    return normalize_domain(org.domain or org.website) if org else None


def _slug(person: Person) -> str | None:
    return person.linkedin_info.public_identifier if person.linkedin_info else None


async def lookup_work_email(
    forager: ForagerClient, person: Person, domain: str | None
) -> str | None:
    """Work email by LinkedIn ID, else by name + domain. Returns None if over budget."""
    try:
        return await forager.work_email(
            linkedin_id=_slug(person),
            first_name=person.first_name,
            last_name=person.last_name,
            domain=domain,
        )
    except CreditBudgetExceededError as exc:
        log.warning("work_email_skipped", reason=str(exc))
        return None


async def lookup_phone(forager: ForagerClient, person: Person, *, enabled: bool) -> str | None:
    """First phone number, only when ENABLE_PHONE_LOOKUP is on (15 credits per lookup)."""
    if not enabled:
        return None
    try:
        phones = await forager.phone_numbers(person_id=person.id, linkedin_id=_slug(person))
    except CreditBudgetExceededError as exc:
        log.warning("phone_skipped", reason=str(exc))
        return None
    return phones[0] if phones else None
