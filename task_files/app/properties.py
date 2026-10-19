"""HubSpot property names this service reads and writes, and the custom ones it creates."""

from typing import Any

GROUP_NAME = "forager_enrichment"
GROUP_LABEL = "Forager enrichment"


def _prop(
    name: str, label: str, *, type_: str = "string", field: str = "text", description: str = ""
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": type_,
        "fieldType": field,
        "groupName": GROUP_NAME,
        "description": description,
    }


def _bookkeeping() -> list[dict[str, Any]]:
    return [
        _prop(
            "forager_enriched_at",
            "Forager enriched at",
            type_="datetime",
            field="date",
            description="When this record was last enriched from Forager.",
        ),
        _prop(
            "forager_enrichment_status",
            "Forager enrichment status",
            description="Result of the last enrichment run.",
        ),
    ]


CONTACT_CUSTOM: list[dict[str, Any]] = [
    _prop("linkedin_url", "LinkedIn URL"),
    _prop("company_linkedin_url", "Company LinkedIn URL"),
    _prop("linkedin_headline", "LinkedIn headline"),
    _prop("linkedin_about", "LinkedIn About", field="textarea"),
    _prop("linkedin_skills", "LinkedIn skills", field="textarea"),
    _prop("current_role_start", "Current role start date"),
    _prop("forager_person_id", "Forager person ID"),
    _prop(
        "enrichment_source",
        "Enrichment source",
        description="forager_finder when this service created the contact.",
    ),
    *_bookkeeping(),
]

COMPANY_CUSTOM: list[dict[str, Any]] = [
    _prop("forager_org_id", "Forager organization ID"),
    _prop("forager_industry", "LinkedIn industry (Forager)"),
    _prop("forager_keywords", "Keywords (Forager)", field="textarea"),
    _prop("forager_employees_range", "Employee range (Forager)"),
    *_bookkeeping(),
]

CONTACT_STANDARD = [
    "firstname",
    "lastname",
    "email",
    "phone",
    "jobtitle",
    "company",
    "website",
    "city",
    "state",
    "country",
]

COMPANY_STANDARD = [
    "name",
    "domain",
    "website",
    "description",
    "industry",
    "annualrevenue",
    "numberofemployees",
    "linkedin_company_page",
    "founded_year",
    "address",
    "city",
    "state",
    "zip",
    "country",
]

CONTACT_READ = CONTACT_STANDARD + [p["name"] for p in CONTACT_CUSTOM]
COMPANY_READ = COMPANY_STANDARD + [p["name"] for p in COMPANY_CUSTOM]

# Bookkeeping fields this service owns; they are always overwritten.
SERVICE_OWNED = frozenset(
    {
        "forager_enriched_at",
        "forager_enrichment_status",
        "forager_person_id",
        "forager_org_id",
        "enrichment_source",
    }
)

# Identity fields a person typed in; never overwritten, even with ENRICHMENT_OVERWRITE=true.
PROTECTED = frozenset({"email", "domain"})

FINDER_SOURCE = "forager_finder"
