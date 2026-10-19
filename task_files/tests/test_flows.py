"""End-to-end enrichment flows against the Forager mock and an in-memory HubSpot."""

from collections.abc import Callable

import pytest

from app.clients.forager import ForagerError
from app.container import Services
from app.properties import FINDER_SOURCE
from tests.fakes import FakeHubSpot

TITLES_5 = ["Account Executive", "Recruiter", "Solutions Engineer", "Partnerships", "Sales"]


async def test_new_company_without_contacts_finds_five_people(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services(default_job_titles=",".join(TITLES_5))
    company_id = hubspot.add("companies", domain="openai.com")

    result = await services.companies.enrich(company_id)

    assert result.status == "enriched"
    company = hubspot.objects["companies"][company_id]
    assert company["name"] == "Openai (mock)"
    assert company["numberofemployees"] == "3500"
    assert company["linkedin_company_page"].startswith("https://www.linkedin.com/company/")
    assert company["forager_enrichment_status"] == "enriched"

    assert result.finder is not None
    assert len(result.finder.created) == 5
    titles = {c.title for c in result.finder.created}
    assert titles == set(TITLES_5)  # one person per title, thanks to interleaving

    contact_ids = hubspot.linked("companies", company_id, "contacts")
    assert len(contact_ids) == 5
    for contact_id in contact_ids:
        contact = hubspot.objects["contacts"][contact_id]
        for field in (
            "firstname",
            "lastname",
            "email",
            "jobtitle",
            "company",
            "linkedin_url",
            "company_linkedin_url",
            "city",
            "state",
            "country",
            "linkedin_headline",
            "linkedin_about",
            "linkedin_skills",
        ):
            assert contact.get(field), f"{field} missing on contact {contact_id}"
        assert contact["email"].endswith("@openai.com")
        assert contact["enrichment_source"] == FINDER_SOURCE
        assert "phone" not in contact

    # 1 company search + 1 people search + 5 emails x 5 credits
    assert services.forager.credit_summary()["spent"] == 27


async def test_company_with_existing_contacts_enriches_them_instead(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")
    contact_id = hubspot.add(
        "contacts",
        firstname="Ada",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace",
    )
    hubspot.associate("companies", company_id, "contacts", contact_id)

    result = await services.companies.enrich(company_id)

    assert result.finder is None
    assert [c.status for c in result.contacts] == ["enriched"]
    assert result.contacts[0].match_method == "linkedin"
    contact = hubspot.objects["contacts"][contact_id]
    assert contact["firstname"] == "Ada"  # existing value kept
    assert contact["email"] == "ada.lovelace@example.com"
    assert contact["jobtitle"]
    assert len(hubspot.objects["contacts"]) == 1


async def test_already_enriched_company_is_skipped(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com", forager_enriched_at="1")
    result = await services.companies.enrich(company_id)
    assert result.status == "skipped"
    assert services.forager.credit_summary()["spent"] == 0


async def test_company_without_domain_is_skipped(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", name="Mystery Co")
    result = await services.companies.enrich(company_id)
    assert result.status == "skipped"
    assert "domain" in result.detail
    assert services.forager.credit_summary()["spent"] == 0


async def test_contact_matched_by_name_and_company(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")
    contact_id = hubspot.add("contacts", firstname="Grace", lastname="Hopper")
    hubspot.associate("contacts", contact_id, "companies", company_id)

    result = await services.contacts.enrich(contact_id)

    assert result.status == "enriched"
    assert result.match_method == "name_and_company"
    assert hubspot.objects["contacts"][contact_id]["email"] == "grace.hopper@openai.com"


async def test_contact_matched_by_email_keeps_email(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    contact_id = hubspot.add("contacts", email="alan.turing@gmail.com")

    result = await services.contacts.enrich(contact_id)

    assert result.match_method == "email"
    contact = hubspot.objects["contacts"][contact_id]
    assert contact["email"] == "alan.turing@gmail.com"
    assert contact["firstname"] == "Alan"
    assert contact["jobtitle"] == "Mock Engineer"
    # reverse email lookup only; no work email bought because the contact had one
    assert services.forager.credit_summary()["spent"] == 5


async def test_contact_created_by_finder_is_skipped(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    """Loop guard: our own contact creations fire contact.creation webhooks too."""
    services = make_services()
    contact_id = hubspot.add("contacts", firstname="A", enrichment_source=FINDER_SOURCE)
    result = await services.contacts.enrich(contact_id)
    assert result.status == "skipped"
    assert services.forager.credit_summary()["spent"] == 0


async def test_contact_without_any_match_data_is_marked_not_found(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    contact_id = hubspot.add("contacts", firstname="Solo")
    result = await services.contacts.enrich(contact_id)
    assert result.status == "not_found"
    assert hubspot.objects["contacts"][contact_id]["forager_enrichment_status"] == "not_found"


async def test_finder_skips_people_without_email_and_stops_at_limit(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")

    result = await services.finder.find(
        company_id=company_id, titles=["CTO", "VP Sales"], limit=4, domain="openai.com", org_id=None
    )

    # Mock returns 4 people; the 4th has no work email.
    assert len(result.candidates) == 4
    assert len(result.created) == 3
    assert [s.reason for s in result.skipped] == ["no work email found"]
    # 2 searches (page 2 is empty) + 3 found emails; the miss is free
    assert services.forager.credit_summary()["spent"] == 2 + 15


async def test_finder_preview_writes_nothing(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")

    result = await services.finder.find(
        company_id=company_id,
        titles=["Recruiter"],
        limit=5,
        domain="openai.com",
        org_id=None,
        preview=True,
    )

    assert [c.title for c in result.candidates] == ["Recruiter", "Recruiter"]
    assert result.created == []
    assert hubspot.objects["contacts"] == {}
    assert services.forager.credit_summary()["spent"] == 1


async def test_finder_does_not_duplicate_existing_people(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")
    kwargs = {
        "company_id": company_id,
        "titles": ["CTO"],
        "limit": 5,
        "domain": "openai.com",
        "org_id": None,
    }
    first = await services.finder.find(**kwargs)
    second = await services.finder.find(**kwargs)
    assert len(first.created) == 2
    assert second.created == []
    assert {s.reason for s in second.skipped} == {"already in HubSpot"}
    assert len(hubspot.objects["contacts"]) == 2


async def test_finder_stops_when_budget_runs_out(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services(forager_credit_budget=12)
    company_id = hubspot.add("companies", domain="openai.com")

    result = await services.finder.find(
        company_id=company_id, titles=TITLES_5, limit=5, domain="openai.com", org_id=None
    )

    # 1 search + 2 emails = 11; a third email would need 5 more
    assert len(result.created) == 2
    assert result.stopped_reason == "credit budget exhausted"
    assert services.forager.credit_summary()["spent"] == 11


async def test_phone_lookup_only_when_enabled(
    make_services: Callable[..., Services], hubspot: FakeHubSpot
) -> None:
    services = make_services(enable_phone_lookup=True, forager_credit_budget=100)
    contact_id = hubspot.add("contacts", linkedin_url="https://www.linkedin.com/in/ada-lovelace")
    await services.contacts.enrich(contact_id)
    assert hubspot.objects["contacts"][contact_id]["phone"] == "+14155550100"
    # 1 role search + 5 email + 15 phone
    assert services.forager.credit_summary()["spent"] == 21


async def test_finder_stops_on_forager_error(
    make_services: Callable[..., Services], hubspot: FakeHubSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable paid result must not be repeated for every remaining candidate."""
    services = make_services()
    company_id = hubspot.add("companies", domain="openai.com")
    calls = 0

    async def broken_email(**_: object) -> str | None:
        nonlocal calls
        calls += 1
        raise ForagerError("format not recognised")

    monkeypatch.setattr(services.forager, "work_email", broken_email)
    result = await services.finder.find(
        company_id=company_id, titles=TITLES_5, limit=5, domain="openai.com", org_id=None
    )
    assert calls == 1
    assert result.created == []
    assert result.stopped_reason == "format not recognised"
