"""Async client for the parts of the HubSpot CRM API this service needs.

Uses CRM v3 for objects, search and properties, and v4 for associations. Authenticates with a
private app access token.
"""

import re
from typing import Any

import httpx
import structlog

from app.clients._http import send_with_retries

log = structlog.get_logger(__name__)

_EXISTING_ID = re.compile(r"Existing ID:\s*(\d+)")


class HubSpotError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class HubSpotClient:
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://api.hubapi.com",
        transport: httpx.AsyncBaseTransport | None = None,
        retry_base_wait: float = 0.5,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )
        self._retry_base_wait = retry_base_wait

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return await send_with_retries(
            self._http, method, path, base_wait=self._retry_base_wait, **kwargs
        )

    async def _request(
        self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any
    ) -> Any:
        response = await self._send(method, path, **kwargs)
        if response.status_code == 404 and allow_404:
            return None
        if response.is_error:
            raise HubSpotError(
                f"HubSpot {method} {path} failed: HTTP {response.status_code}: "
                f"{response.text[:500]}",
                response.status_code,
                response.text,
            )
        if not response.content:
            return None
        return response.json()

    # --- objects -------------------------------------------------------------------------

    async def get_object(
        self, object_type: str, object_id: str, properties: list[str]
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._request(
            "GET",
            f"/crm/v3/objects/{object_type}/{object_id}",
            params={"properties": ",".join(properties)},
            allow_404=True,
        )
        return result

    async def update_object(
        self, object_type: str, object_id: str, properties: dict[str, str]
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._request(
            "PATCH",
            f"/crm/v3/objects/{object_type}/{object_id}",
            json={"properties": properties},
        )
        return result

    async def create_contact(self, properties: dict[str, str]) -> tuple[str, bool]:
        """Create a contact. Returns `(contact_id, created)`.

        If a contact with the same email already exists, HubSpot answers 409 with its ID; that
        contact is updated instead, so a re-run never creates duplicates.
        """
        response = await self._send(
            "POST", "/crm/v3/objects/contacts", json={"properties": properties}
        )
        if response.status_code == 409:
            match = _EXISTING_ID.search(response.text)
            if match:
                existing_id = match.group(1)
                await self.update_object("contacts", existing_id, properties)
                return existing_id, False
        if response.is_error:
            raise HubSpotError(
                f"HubSpot contact create failed: HTTP {response.status_code}: "
                f"{response.text[:500]}",
                response.status_code,
                response.text,
            )
        return str(response.json()["id"]), True

    async def search_contacts(
        self, property_name: str, value: str, properties: list[str], limit: int = 1
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "POST",
            "/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": property_name, "operator": "EQ", "value": value}]}
                ],
                "properties": properties,
                "limit": limit,
            },
        )
        return list((data or {}).get("results", []))

    # --- associations --------------------------------------------------------------------

    async def associated_ids(self, from_type: str, object_id: str, to_type: str) -> list[str]:
        ids: list[str] = []
        params: dict[str, Any] = {"limit": 500}
        while True:
            data = await self._request(
                "GET",
                f"/crm/v4/objects/{from_type}/{object_id}/associations/{to_type}",
                params=params,
                allow_404=True,
            )
            if not data:
                return ids
            ids.extend(str(r["toObjectId"]) for r in data.get("results", []))
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return ids
            params["after"] = after

    async def associate_default(
        self, from_type: str, from_id: str, to_type: str, to_id: str
    ) -> None:
        await self._request(
            "PUT", f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}"
        )

    # --- properties ----------------------------------------------------------------------

    async def get_property(self, object_type: str, name: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._request(
            "GET", f"/crm/v3/properties/{object_type}/{name}", allow_404=True
        )
        return result

    async def enum_options(self, object_type: str, name: str) -> dict[str, str]:
        """Return a dropdown property's options as `{label: value}`."""
        prop = await self.get_property(object_type, name)
        return {
            str(o["label"]): str(o["value"])
            for o in (prop or {}).get("options", [])
            if not o.get("hidden")
        }

    async def create_property_group(self, object_type: str, name: str, label: str) -> bool:
        """Create a property group. Returns False if it already exists."""
        response = await self._send(
            "POST",
            f"/crm/v3/properties/{object_type}/groups",
            json={"name": name, "label": label},
        )
        if response.status_code == 409:
            return False
        if response.is_error:
            raise HubSpotError(
                f"HubSpot property group create failed: {response.text[:500]}",
                response.status_code,
                response.text,
            )
        return True

    async def create_property(self, object_type: str, definition: dict[str, Any]) -> bool:
        """Create a custom property. Returns False if it already exists."""
        response = await self._send("POST", f"/crm/v3/properties/{object_type}", json=definition)
        if response.status_code == 409:
            return False
        if response.is_error:
            raise HubSpotError(
                f"HubSpot property create failed for {definition.get('name')}: "
                f"{response.text[:500]}",
                response.status_code,
                response.text,
            )
        return True
