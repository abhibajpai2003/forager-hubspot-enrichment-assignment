import pytest

from app.mapping import (
    clean_name,
    company_properties,
    contact_properties,
    email_domain,
    linkedin_slug,
    match_industry,
    merge_properties,
    normalize_domain,
    split_location,
)
from app.models import Location, Organization, Person, PersonRole

ORG = Organization.model_validate(
    {
        "id": 42,
        "name": "OpenAI",
        "website": "https://openai.com/",
        "domain": "openai.com",
        "description": "AI research and deployment company.",
        "founded_date": "2015-12-11",
        "employees_amount": 3500,
        "employees_range": "1001-5000",
        "keywords": [{"id": 1, "name": "artificial intelligence"}, {"id": 2, "name": None}],
        "finance_info": {"revenue": 3700000000},
        "linkedin_info": {
            "public_identifier": "openai",
            "public_profile_url": "https://www.linkedin.com/company/openai",
            "industry": {"id": 1, "name": "Computer Software"},
        },
        "addresses": [
            {
                "street_number": "1455",
                "street_name": "3rd Street",
                "city": "San Francisco",
                "state": "California",
                "postcode": "94158",
                "country": "United States",
                "summary": "...",
            }
        ],
        "location": None,
    }
)

PERSON = Person.model_validate(
    {
        "id": 7,
        "full_name": "Ada Lovelace",
        "first_name": "ADA",
        "last_name": "lovelace",
        "headline": "Head of Partnerships at OpenAI",
        "description": "About me.",
        "skills": [{"name": "Partnerships"}, {"name": "Negotiation"}],
        "location": {
            "id": 1,
            "name": "San Francisco, California, United States",
            "osm_locations": [],
        },
        "linkedin_info": {"public_profile_url": "https://www.linkedin.com/in/ada-lovelace"},
    }
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.OpenAI.com/about", "openai.com"),
        ("openai.com", "openai.com"),
        ("www.openai.com", "openai.com"),
        ("http://sub.example.co.uk:8080/x", "sub.example.co.uk"),
        ("localhost", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_domain(value: str | None, expected: str | None) -> None:
    assert normalize_domain(value) == expected


def test_email_domain_ignores_free_mail() -> None:
    assert email_domain("sam@openai.com") == "openai.com"
    assert email_domain("sam@gmail.com") is None
    assert email_domain("not-an-email") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.linkedin.com/in/ada-lovelace/", "ada-lovelace"),
        ("linkedin.com/company/openai?trk=x", "openai"),
        ("ada-lovelace", "ada-lovelace"),
        ("https://example.com/ada", None),
        (None, None),
    ],
)
def test_linkedin_slug(value: str | None, expected: str | None) -> None:
    assert linkedin_slug(value) == expected


def test_clean_name_fixes_case_only_when_needed() -> None:
    assert clean_name("ADA") == "Ada"
    assert clean_name("  mary   ann ") == "Mary Ann"
    assert clean_name("McDonald") == "McDonald"
    assert clean_name(None) is None


def test_split_location_prefers_structured_parts() -> None:
    location = Location.model_validate(
        {
            "name": "Somewhere odd",
            "osm_locations": [
                {"name": "Austin", "place_type": "city"},
                {"name": "Texas", "place_type": "state"},
                {"name": "United States", "place_type": "country"},
            ],
        }
    )
    assert split_location(location) == ("Austin", "Texas", "United States")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("London, England, United Kingdom", ("London", "England", "United Kingdom")),
        ("California, United States", (None, "California", "United States")),
        ("Germany", (None, None, "Germany")),
    ],
)
def test_split_location_falls_back_to_display_name(
    name: str, expected: tuple[str | None, ...]
) -> None:
    assert split_location(Location(name=name)) == expected


def test_match_industry() -> None:
    options = {"Computer Software": "COMPUTER_SOFTWARE", "Research": "RESEARCH"}
    assert match_industry("Computer software", options) == "COMPUTER_SOFTWARE"
    assert match_industry("Research Services", options) is None
    assert match_industry(None, options) is None


def test_company_properties() -> None:
    props = company_properties(ORG, {"Computer Software": "COMPUTER_SOFTWARE"})
    assert props == {
        "name": "OpenAI",
        "domain": "openai.com",
        "website": "https://openai.com/",
        "description": "AI research and deployment company.",
        "annualrevenue": "3700000000",
        "numberofemployees": "3500",
        "linkedin_company_page": "https://www.linkedin.com/company/openai",
        "founded_year": "2015",
        "address": "1455 3rd Street",
        "city": "San Francisco",
        "state": "California",
        "zip": "94158",
        "country": "United States",
        "industry": "COMPUTER_SOFTWARE",
        "forager_industry": "Computer Software",
        "forager_keywords": "artificial intelligence",
        "forager_employees_range": "1001-5000",
        "forager_org_id": "42",
    }


def test_contact_properties() -> None:
    role = PersonRole(role_title="Head of Partnerships", start_date="2023-04-01")
    props = contact_properties(PERSON, role, ORG, email="Ada@OpenAI.com")
    assert props == {
        "firstname": "Ada",
        "lastname": "Lovelace",
        "email": "ada@openai.com",
        "jobtitle": "Head of Partnerships",
        "company": "OpenAI",
        "website": "openai.com",
        "company_linkedin_url": "https://www.linkedin.com/company/openai",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
        "city": "San Francisco",
        "state": "California",
        "country": "United States",
        "linkedin_headline": "Head of Partnerships at OpenAI",
        "linkedin_about": "About me.",
        "linkedin_skills": "Partnerships, Negotiation",
        "current_role_start": "2023-04-01",
        "forager_person_id": "7",
    }
    assert "phone" not in props


def test_merge_keeps_existing_values_by_default() -> None:
    current = {"name": "Open AI", "description": "", "email": "a@b.com"}
    new = {
        "name": "OpenAI",
        "description": "New",
        "email": "x@y.com",
        "forager_enrichment_status": "enriched",
    }
    assert merge_properties(current, new, overwrite=False) == {
        "description": "New",
        "forager_enrichment_status": "enriched",
    }


def test_merge_overwrite_never_touches_identity_fields() -> None:
    current = {"name": "Open AI", "email": "a@b.com", "domain": "openai.com"}
    new = {"name": "OpenAI", "email": "x@y.com", "domain": "other.com"}
    assert merge_properties(current, new, overwrite=True) == {"name": "OpenAI"}


def test_lenient_models_accept_nulls() -> None:
    org = Organization.model_validate({"name": "X", "keywords": None, "addresses": None})
    assert org.keywords == []
    assert org.addresses == []
