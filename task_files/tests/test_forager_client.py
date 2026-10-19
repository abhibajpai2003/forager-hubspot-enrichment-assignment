from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from app.cache import ResponseCache
from app.clients.forager import (
    ForagerClient,
    ForagerError,
    pick_work_email,
    role_search_payload,
    title_query,
)
from app.credits import CreditBudgetExceededError, CreditLedger

BASE = "https://api-v2.forager.ai"
ACCOUNT = 1234
EMAIL_SUBMIT = f"{BASE}/api/v2/{ACCOUNT}/datastorage/person_contacts_lookup/work_emails/"
ROLE_SEARCH = f"{BASE}/api/{ACCOUNT}/datastorage/person_role_search/"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[ForagerClient]:
    forager = ForagerClient(
        base_url=BASE,
        api_key="secret-key",
        account_id=ACCOUNT,
        ledger=CreditLedger(budget=45),
        cache=ResponseCache(tmp_path),
        poll_interval=0,
        retry_base_wait=0,
    )
    yield forager
    await forager.aclose()


def test_title_query_quotes_phrases() -> None:
    assert (
        title_query(["VP Sales", " CTO ", 'Head of "AI"']) == '"VP Sales" OR "CTO" OR "Head of AI"'
    )


def test_role_search_payload_only_sets_given_filters() -> None:
    assert role_search_payload(organization_ids=[9], role_title='"CTO"') == {
        "page": 1,
        "role_is_current": True,
        "organizations": [9],
        "role_title": '"CTO"',
    }


@respx.mock
async def test_role_search_sends_key_and_account_and_caches(client: ForagerClient) -> None:
    route = respx.post(ROLE_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "search_results": [
                    {"id": 1, "role_title": "CTO", "is_current": True, "person": {"id": 5}}
                ],
                "total_search_results": 1,
            },
        )
    )
    payload = role_search_payload(organization_domains=["openai.com"], role_title='"CTO"')
    first = await client.person_role_search(payload)
    second = await client.person_role_search(payload)

    assert route.call_count == 1  # second call served from cache
    assert route.calls[0].request.headers["X-API-KEY"] == "secret-key"
    assert first[0].person is not None and first[0].person.id == 5
    assert second == first
    assert client.credits_remaining == 44


@respx.mock
async def test_retries_429_then_succeeds(client: ForagerClient) -> None:
    route = respx.post(ROLE_SEARCH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"search_results": [], "total_search_results": 0}),
        ]
    )
    assert await client.person_role_search(role_search_payload(role_title='"CTO"')) == []
    assert route.call_count == 2


@respx.mock
async def test_client_error_raises_and_is_not_charged(client: ForagerClient) -> None:
    respx.post(ROLE_SEARCH).mock(return_value=httpx.Response(400, json={"detail": "bad"}))
    with pytest.raises(ForagerError):
        await client.person_role_search(role_search_payload(role_title="x y"))
    assert client.credits_remaining == 45


@respx.mock
async def test_work_email_polls_until_done(client: ForagerClient) -> None:
    respx.post(EMAIL_SUBMIT).mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "j1", "status": "queued", "status_check_url": "", "webhook_url": ""},
        )
    )
    poll = respx.get(f"{EMAIL_SUBMIT}j1/").mock(
        side_effect=[
            httpx.Response(200, json={"job_id": "j1", "status": "running"}),
            httpx.Response(
                200,
                json={
                    "job_id": "j1",
                    "status": "completed",
                    "result": "found",
                    "emails": ["ada@openai.com"],
                },
            ),
        ]
    )
    email = await client.work_email(linkedin_id="ada-lovelace")
    assert email == "ada@openai.com"
    assert poll.call_count == 2
    assert client.credits_remaining == 40

    # Cached: no new job, no new charge.
    assert await client.work_email(linkedin_id="ada-lovelace") == "ada@openai.com"
    assert client.credits_remaining == 40


@respx.mock
async def test_work_email_not_found_is_free(client: ForagerClient) -> None:
    respx.post(EMAIL_SUBMIT).mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "j2", "status": "queued", "status_check_url": "", "webhook_url": ""},
        )
    )
    respx.get(f"{EMAIL_SUBMIT}j2/").mock(
        return_value=httpx.Response(
            200, json={"job_id": "j2", "status": "completed", "result": "not_found", "emails": []}
        )
    )
    assert await client.work_email(linkedin_id="nobody") is None
    assert client.credits_remaining == 45


@respx.mock
async def test_unreadable_found_email_stops_and_is_reused_from_cache(
    client: ForagerClient,
) -> None:
    respx.post(EMAIL_SUBMIT).mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "j3", "status": "queued", "status_check_url": "", "webhook_url": ""},
        )
    )
    respx.get(f"{EMAIL_SUBMIT}j3/").mock(
        return_value=httpx.Response(
            200,
            json={"job_id": "j3", "status": "completed", "result": "found", "emails": [{"x": 1}]},
        )
    )
    with pytest.raises(ForagerError, match="not recognised"):
        await client.work_email(linkedin_id="odd-shape")
    assert client.credits_remaining == 40  # Forager charged for a found email

    # A second attempt is served from the cache: still unreadable, but no new charge.
    with pytest.raises(ForagerError):
        await client.work_email(linkedin_id="odd-shape")
    assert client.credits_remaining == 40


async def test_work_email_needs_enough_input(client: ForagerClient) -> None:
    assert await client.work_email(first_name="Ada") is None


@respx.mock
async def test_budget_blocks_call_before_sending(tmp_path: Path) -> None:
    route = respx.post(EMAIL_SUBMIT)
    forager = ForagerClient(
        base_url=BASE,
        api_key="k",
        account_id=ACCOUNT,
        ledger=CreditLedger(budget=4),
        cache=ResponseCache(None),
    )
    with pytest.raises(CreditBudgetExceededError):
        await forager.work_email(linkedin_id="ada")
    assert route.call_count == 0
    await forager.aclose()


@respx.mock
async def test_not_found_lookup_returns_none(client: ForagerClient) -> None:
    respx.post(f"{BASE}/api/{ACCOUNT}/datastorage/person_detail_reverse_lookup/by_email/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    assert await client.reverse_lookup_by_email("ghost@openai.com") is None


@pytest.mark.parametrize(
    ("emails", "expected"),
    [
        (["a@x.com", "longer@x.com"], "a@x.com"),
        ([["risky@x.com", "risky"], ["good@x.com", "valid"]], "good@x.com"),
        ([{"email": "Obj@X.com", "validation_status": "valid"}], "obj@x.com"),
        (["a@x.com (valid)"], "a@x.com"),
        ([], None),
    ],
)
def test_pick_work_email_shapes(emails: list[object], expected: str | None) -> None:
    job = {"status": "completed", "result": "found", "emails": emails}
    assert pick_work_email(job) == expected


def test_pick_work_email_ignores_unfinished_jobs() -> None:
    assert pick_work_email({"status": "running", "emails": ["a@x.com"]}) is None
