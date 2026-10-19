import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeHubSpot

PUBLIC_URL = "https://enrich.up.railway.app"
ADMIN = {"X-Admin-Key": "admin-key"}


def signed(body: bytes, secret: str = "test-secret") -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    source = b"POST" + f"{PUBLIC_URL}/webhooks/hubspot".encode() + body + timestamp.encode()
    signature = base64.b64encode(hmac.new(secret.encode(), source, hashlib.sha256).digest())
    return {
        "Content-Type": "application/json",
        "X-HubSpot-Signature-v3": signature.decode(),
        "X-HubSpot-Request-Timestamp": timestamp,
    }


@pytest.fixture
def client(make_settings: Callable[..., Settings], hubspot: FakeHubSpot) -> Iterator[TestClient]:
    settings = make_settings(public_base_url=PUBLIC_URL, default_job_titles="CTO,VP Sales")
    app = create_app(settings, hubspot_transport=hubspot.transport())
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "forager_mode": "mock"}


def test_webhook_rejects_bad_signature(client: TestClient) -> None:
    body = b"[]"
    headers = signed(body, secret="wrong")
    assert client.post("/webhooks/hubspot", content=body, headers=headers).status_code == 401


def test_company_webhook_enriches_and_finds_contacts(
    client: TestClient, hubspot: FakeHubSpot
) -> None:
    company_id = hubspot.add("companies", domain="openai.com")
    body = json.dumps(
        [{"eventId": 11, "subscriptionType": "company.creation", "objectId": int(company_id)}]
    ).encode()

    response = client.post("/webhooks/hubspot", content=body, headers=signed(body))

    assert response.status_code == 200
    assert response.json() == {"received": 1, "scheduled": 1}
    # TestClient runs background tasks before returning.
    assert hubspot.objects["companies"][company_id]["forager_enrichment_status"] == "enriched"
    # 2 titles x 2 people in the mock; one of the four has no work email
    assert len(hubspot.linked("companies", company_id, "contacts")) == 3


def test_duplicate_event_is_ignored(client: TestClient, hubspot: FakeHubSpot) -> None:
    contact_id = hubspot.add("contacts", firstname="Solo")
    body = json.dumps(
        [{"eventId": 12, "subscriptionType": "contact.creation", "objectId": int(contact_id)}]
    ).encode()
    assert (
        client.post("/webhooks/hubspot", content=body, headers=signed(body)).json()["scheduled"]
        == 1
    )
    assert (
        client.post("/webhooks/hubspot", content=body, headers=signed(body)).json()["scheduled"]
        == 0
    )


def test_unrelated_events_are_ignored(client: TestClient) -> None:
    body = json.dumps(
        [{"eventId": 13, "subscriptionType": "deal.creation", "objectId": 5}]
    ).encode()
    assert client.post("/webhooks/hubspot", content=body, headers=signed(body)).json() == {
        "received": 1,
        "scheduled": 0,
    }


def test_admin_endpoints_need_key(client: TestClient) -> None:
    assert client.post("/enrich/company/1").status_code == 401
    assert client.post("/enrich/company/1", headers={"X-Admin-Key": "nope"}).status_code == 401


def test_find_contacts_preview(client: TestClient, hubspot: FakeHubSpot) -> None:
    company_id = hubspot.add("companies", domain="openai.com")
    response = client.post(
        f"/companies/{company_id}/find-contacts",
        json={"titles": ["Recruiter"], "preview": True},
        headers=ADMIN,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["preview"] is True
    assert [c["title"] for c in data["candidates"]] == ["Recruiter", "Recruiter"]
    assert data["created"] == []


def test_find_contacts_unknown_company(client: TestClient) -> None:
    response = client.post("/companies/999/find-contacts", json={}, headers=ADMIN)
    assert response.status_code == 404


def test_manual_contact_enrichment_and_credits(client: TestClient, hubspot: FakeHubSpot) -> None:
    contact_id = hubspot.add("contacts", linkedin_url="https://www.linkedin.com/in/ada-lovelace")
    response = client.post(f"/enrich/contact/{contact_id}", headers=ADMIN)
    assert response.json()["status"] == "enriched"

    again = client.post(f"/enrich/contact/{contact_id}", headers=ADMIN)
    assert again.json()["status"] == "skipped"

    credits = client.get("/credits", headers=ADMIN).json()
    assert credits["mode"] == "mock"
    assert credits["spent"] == 6
