"""Pure functions that turn Forager records into clean HubSpot property values."""

import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.models import Location, Organization, Person, PersonRole
from app.properties import PROTECTED, SERVICE_OWNED

FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "yandex.com",
    }
)

CITY_TYPES = frozenset({"city", "town", "village", "locality", "municipality", "hamlet"})
STATE_TYPES = frozenset({"state", "region", "province", "administrative_area_level_1"})

_LINKEDIN_PATH = re.compile(r"linkedin\.com/(in|company|school)/([^/?#]+)", re.IGNORECASE)


def now_ms() -> str:
    """Current time in the format HubSpot datetime properties accept (epoch milliseconds)."""
    return str(int(time.time() * 1000))


def normalize_domain(value: str | None) -> str | None:
    """`https://www.OpenAI.com/about` -> `openai.com`."""
    if not value or not value.strip():
        return None
    text = value.strip().lower()
    if "://" not in text:
        text = "http://" + text
    host = urlsplit(text).hostname
    if not host:
        return None
    host = host.removeprefix("www.")
    return host if "." in host else None


def email_domain(email: str | None) -> str | None:
    """Company domain from a work email; None for free mail providers."""
    if not email or "@" not in email:
        return None
    domain = normalize_domain(email.rsplit("@", 1)[1])
    return None if domain in FREE_EMAIL_DOMAINS else domain


def linkedin_slug(value: str | None) -> str | None:
    """Public identifier from a LinkedIn URL (`/in/<slug>` or `/company/<slug>`), or a bare slug."""
    if not value or not value.strip():
        return None
    text = value.strip()
    match = _LINKEDIN_PATH.search(text)
    if match:
        return match.group(2)
    if "/" not in text and "." not in text and " " not in text:
        return text
    return None


def clean_name(value: str | None) -> str | None:
    """Trim and collapse whitespace; fix ALL-CAPS or all-lowercase names, keep the rest as is."""
    if not value:
        return None
    name = " ".join(value.split())
    if name.isupper() or name.islower():
        name = name.title()
    return name or None


def split_location(location: Location | None) -> tuple[str | None, str | None, str | None]:
    """Return (city, state, country) from a Forager location.

    Uses the structured OpenStreetMap parts when present, and falls back to the display name
    (`"San Francisco, California, United States"`).
    """
    if location is None:
        return None, None, None
    city = state = country = None
    for osm in location.osm_locations:
        place_type = (osm.place_type or "").lower()
        if place_type in CITY_TYPES and city is None:
            city = osm.name
        elif place_type in STATE_TYPES and state is None:
            state = osm.name
        elif place_type == "country" and country is None:
            country = osm.name

    parts = [p.strip() for p in (location.name or "").split(",") if p.strip()]
    if len(parts) >= 3:
        city, state, country = city or parts[0], state or parts[-2], country or parts[-1]
    elif len(parts) == 2:
        state, country = state or parts[0], country or parts[1]
    elif len(parts) == 1 and not (city or state):
        country = country or parts[0]
    return city, state, country


def match_industry(name: str | None, options: Mapping[str, str]) -> str | None:
    """Map a LinkedIn industry name onto one of HubSpot's `industry` dropdown values."""
    if not name:
        return None

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    target = norm(name)
    for label, value in options.items():
        if norm(label) == target or norm(value) == target:
            return value
    return None


def _drop_empty(props: Mapping[str, str | None]) -> dict[str, str]:
    return {k: v.strip() for k, v in props.items() if v is not None and v.strip()}


def company_properties(
    org: Organization, industry_options: Mapping[str, str] | None = None
) -> dict[str, str]:
    linkedin = org.linkedin_info
    industry_name = linkedin.industry.name if linkedin and linkedin.industry else None
    city, state, country = split_location(org.location)
    street = zip_code = None
    if org.addresses:
        hq = org.addresses[0]
        city, state, country = hq.city or city, hq.state or state, hq.country or country
        street = " ".join(p for p in (hq.street_number, hq.street_name) if p) or None
        zip_code = hq.postcode
    return _drop_empty(
        {
            "name": org.name,
            "domain": normalize_domain(org.domain or org.website),
            "website": org.website,
            "description": org.description,
            "annualrevenue": str(org.finance_info.revenue)
            if org.finance_info and org.finance_info.revenue
            else None,
            "numberofemployees": str(org.employees_amount) if org.employees_amount else None,
            "linkedin_company_page": linkedin.public_profile_url if linkedin else None,
            "founded_year": org.founded_date[:4] if org.founded_date else None,
            "address": street,
            "city": city,
            "state": state,
            "zip": zip_code,
            "country": country,
            "industry": match_industry(industry_name, industry_options or {}),
            "forager_industry": industry_name,
            "forager_keywords": ", ".join(k.name for k in org.keywords[:40] if k.name) or None,
            "forager_employees_range": org.employees_range,
            "forager_org_id": str(org.id) if org.id else None,
        }
    )


def contact_properties(
    person: Person,
    role: PersonRole | None,
    org: Organization | None,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, str]:
    city, state, country = split_location(person.location)
    person_li = person.linkedin_info
    org_li = org.linkedin_info if org else None
    company_name = (org.name if org else None) or (role.organization_name if role else None)
    skills = ", ".join(s.name for s in person.skills[:30] if s.name) or None
    return _drop_empty(
        {
            "firstname": clean_name(person.first_name),
            "lastname": clean_name(person.last_name),
            "email": email.lower() if email else None,
            "phone": phone,
            "jobtitle": role.role_title if role else None,
            "company": company_name,
            "website": normalize_domain(org.domain or org.website) if org else None,
            "company_linkedin_url": org_li.public_profile_url if org_li else None,
            "linkedin_url": person_li.public_profile_url if person_li else None,
            "city": city,
            "state": state,
            "country": country,
            "linkedin_headline": person.headline,
            "linkedin_about": person.description,
            "linkedin_skills": skills,
            "current_role_start": role.start_date if role else None,
            "forager_person_id": str(person.id) if person.id else None,
        }
    )


def merge_properties(
    current: Mapping[str, str | None], new: Mapping[str, str], *, overwrite: bool
) -> dict[str, str]:
    """Return only the properties that should be written.

    Empty values never replace existing ones (they are dropped before this point). Existing
    values are kept unless `overwrite` is set; identity fields are never replaced, and the
    service's own bookkeeping fields are always written.
    """
    changes: dict[str, str] = {}
    for key, value in new.items():
        existing = (current.get(key) or "").strip()
        if existing == value:
            continue
        if existing and key not in SERVICE_OWNED and (key in PROTECTED or not overwrite):
            continue
        changes[key] = value
    return changes
