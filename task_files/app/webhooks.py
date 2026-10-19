"""HubSpot webhook handling: signature checks, event parsing and de-duplication."""

import base64
import hashlib
import hmac
import re
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Literal

from starlette.requests import Request

from app.models import HubSpotEvent

MAX_SIGNATURE_AGE_MS = 5 * 60 * 1000

# HubSpot's v3 signing decodes only these percent-escapes in the URI.
_V3_DECODE = {
    "%3A": ":",
    "%2F": "/",
    "%3F": "?",
    "%40": "@",
    "%21": "!",
    "%24": "$",
    "%27": "'",
    "%28": "(",
    "%29": ")",
    "%2A": "*",
    "%2C": ",",
    "%3B": ";",
}
_V3_DECODE_RE = re.compile("|".join(_V3_DECODE), re.IGNORECASE)

ObjectKind = Literal["company", "contact"]


def decode_v3_uri(uri: str) -> str:
    return _V3_DECODE_RE.sub(lambda m: _V3_DECODE[m.group(0).upper()], uri)


def request_uri(request: Request, public_base_url: str | None) -> str:
    """The full URL HubSpot called, as it was signed.

    Behind Railway's proxy the app sees plain http, so the public scheme and host come from
    PUBLIC_BASE_URL, or else from the X-Forwarded-* headers.
    """
    raw_path = request.scope.get("raw_path") or request.url.path.encode()
    path = raw_path.decode("latin-1")
    query = request.scope.get("query_string", b"").decode("latin-1")
    if public_base_url:
        base = public_base_url.rstrip("/")
    else:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        base = f"{proto}://{host}"
    return f"{base}{path}" + (f"?{query}" if query else "")


def verify_signature(
    *,
    secret: str,
    method: str,
    uri: str,
    body: bytes,
    headers: Mapping[str, str],
    now_ms: int | None = None,
) -> bool:
    """Validate a HubSpot request signature (v3, falling back to v1/v2).

    https://developers.hubspot.com/docs/apps/legacy-apps/authentication/validating-requests
    """
    signature_v3 = headers.get("x-hubspot-signature-v3")
    if signature_v3:
        timestamp = headers.get("x-hubspot-request-timestamp", "")
        if not timestamp.isdigit():
            return False
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if abs(now - int(timestamp)) > MAX_SIGNATURE_AGE_MS:
            return False
        source = method.upper().encode() + decode_v3_uri(uri).encode() + body + timestamp.encode()
        digest = hmac.new(secret.encode(), source, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature_v3)

    signature = headers.get("x-hubspot-signature")
    if not signature:
        return False
    version = headers.get("x-hubspot-signature-version", "v1").lower()
    if version == "v1":
        source = secret.encode() + body
    elif version == "v2":
        source = secret.encode() + method.upper().encode() + uri.encode() + body
    else:
        return False
    expected = hashlib.sha256(source).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def parse_events(payload: Any) -> list[HubSpotEvent]:
    """HubSpot sends a JSON array of up to 100 events."""
    items = payload if isinstance(payload, list) else [payload]
    return [HubSpotEvent.model_validate(item) for item in items if isinstance(item, dict)]


def event_kind(event: HubSpotEvent) -> ObjectKind | None:
    """Which object a creation event is about; None for events this service ignores."""
    subscription = (event.subscriptionType or "").lower()
    object_type = event.objectTypeId if subscription == "object.creation" else None
    if subscription == "company.creation" or object_type == "0-2":
        return "company"
    if subscription == "contact.creation" or object_type == "0-1":
        return "contact"
    return None


class EventDeduper:
    """Remembers recent event IDs; HubSpot delivers at least once, so duplicates happen."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_size = max_size

    def seen_before(self, key: str) -> bool:
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False
