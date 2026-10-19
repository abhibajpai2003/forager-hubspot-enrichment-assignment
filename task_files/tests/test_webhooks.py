import base64
import hashlib
import hmac

from app.models import HubSpotEvent
from app.webhooks import EventDeduper, decode_v3_uri, event_kind, parse_events, verify_signature

SECRET = "client-secret"
URI = "https://svc.up.railway.app/webhooks/hubspot"
BODY = b'[{"eventId":1,"subscriptionType":"company.creation","objectId":5}]'
NOW = 1_760_000_000_000


def v3_headers(
    body: bytes = BODY, uri: str = URI, timestamp: int = NOW, method: str = "POST"
) -> dict[str, str]:
    source = method.encode() + uri.encode() + body + str(timestamp).encode()
    digest = hmac.new(SECRET.encode(), source, hashlib.sha256).digest()
    return {
        "x-hubspot-signature-v3": base64.b64encode(digest).decode(),
        "x-hubspot-request-timestamp": str(timestamp),
    }


def check(headers: dict[str, str], body: bytes = BODY, uri: str = URI) -> bool:
    return verify_signature(
        secret=SECRET, method="POST", uri=uri, body=body, headers=headers, now_ms=NOW
    )


def test_v3_signature_valid() -> None:
    assert check(v3_headers())


def test_v3_rejects_tampered_body() -> None:
    assert not check(v3_headers(), body=BODY.replace(b"5", b"6"))


def test_v3_rejects_stale_timestamp() -> None:
    assert not check(v3_headers(timestamp=NOW - 6 * 60 * 1000))


def test_v3_rejects_wrong_secret() -> None:
    headers = v3_headers()
    assert not verify_signature(
        secret="other", method="POST", uri=URI, body=BODY, headers=headers, now_ms=NOW
    )


def test_v3_decodes_only_listed_escapes() -> None:
    assert decode_v3_uri("https://x.io/a%3Ab%2fc?q=%20%40") == "https://x.io/a:b/c?q=%20@"
    signed_uri = "https://x.io/hook?a=b:c"
    headers = v3_headers(uri=signed_uri)
    assert check(headers, uri="https://x.io/hook?a=b%3Ac")


def test_v1_signature() -> None:
    signature = hashlib.sha256(SECRET.encode() + BODY).hexdigest()
    assert check({"x-hubspot-signature": signature, "x-hubspot-signature-version": "v1"})
    assert not check({"x-hubspot-signature": "0" * 64, "x-hubspot-signature-version": "v1"})


def test_missing_signature_is_rejected() -> None:
    assert not check({})


def test_event_kind() -> None:
    assert event_kind(HubSpotEvent(subscriptionType="company.creation")) == "company"
    assert event_kind(HubSpotEvent(subscriptionType="contact.creation")) == "contact"
    assert event_kind(HubSpotEvent(subscriptionType="object.creation", objectTypeId="0-2")) == (
        "company"
    )
    assert event_kind(HubSpotEvent(subscriptionType="object.creation", objectTypeId="0-1")) == (
        "contact"
    )
    assert event_kind(HubSpotEvent(subscriptionType="contact.propertyChange")) is None


def test_parse_events_accepts_list_or_single() -> None:
    assert len(parse_events([{"eventId": 1}, {"eventId": 2}, "junk"])) == 2
    assert len(parse_events({"eventId": 1})) == 1


def test_deduper() -> None:
    deduper = EventDeduper(max_size=2)
    assert not deduper.seen_before("a")
    assert deduper.seen_before("a")
    deduper.seen_before("b")
    deduper.seen_before("c")
    assert not deduper.seen_before("a")
