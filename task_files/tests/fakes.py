"""An in-memory HubSpot CRM, served through httpx.MockTransport."""

import json
import re
from typing import Any

import httpx

INDUSTRY_OPTIONS = [
    {"label": "Computer Software", "value": "COMPUTER_SOFTWARE", "hidden": False},
    {"label": "Research", "value": "RESEARCH", "hidden": False},
]


class FakeHubSpot:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, dict[str, str]]] = {"companies": {}, "contacts": {}}
        self.associations: set[tuple[str, str, str, str]] = set()
        self.created_properties: dict[str, list[str]] = {"companies": [], "contacts": []}
        self.patches: list[tuple[str, str, dict[str, str]]] = []
        self._next_id = 1000

    # --- test helpers --------------------------------------------------------------------

    def add(self, object_type: str, **props: str) -> str:
        self._next_id += 1
        object_id = str(self._next_id)
        self.objects[object_type][object_id] = dict(props)
        return object_id

    def associate(self, a_type: str, a_id: str, b_type: str, b_id: str) -> None:
        self.associations.add((a_type, a_id, b_type, b_id))
        self.associations.add((b_type, b_id, a_type, a_id))

    def linked(self, from_type: str, object_id: str, to_type: str) -> list[str]:
        return sorted(
            b_id
            for (a_t, a_id, b_t, b_id) in self.associations
            if a_t == from_type and a_id == object_id and b_t == to_type
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # --- request handling ----------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: Any = json.loads(request.content) if request.content else None

        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/search", path):
            return self._search(m.group(1), body)
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/(\d+)", path):
            return self._object(request.method, m.group(1), m.group(2), body)
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)", path):
            return self._create(m.group(1), body)
        if m := re.fullmatch(r"/crm/v4/objects/(\w+)/(\d+)/associations/default/(\w+)/(\d+)", path):
            self.associate(*m.groups())
            return httpx.Response(200, json={"status": "COMPLETE"})
        if m := re.fullmatch(r"/crm/v4/objects/(\w+)/(\d+)/associations/(\w+)", path):
            ids = self.linked(m.group(1), m.group(2), m.group(3))
            return httpx.Response(200, json={"results": [{"toObjectId": int(i)} for i in ids]})
        if path == "/crm/v3/properties/companies/industry":
            return httpx.Response(200, json={"name": "industry", "options": INDUSTRY_OPTIONS})
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)/groups", path):
            return httpx.Response(201, json=body)
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)", path):
            if body["name"] in self.created_properties[m.group(1)]:
                return httpx.Response(409, json={"message": "exists"})
            self.created_properties[m.group(1)].append(body["name"])
            return httpx.Response(201, json=body)
        if re.fullmatch(r"/crm/v3/properties/(\w+)/(\w+)", path):
            return httpx.Response(200, json={"name": path.rsplit("/", 1)[-1]})
        return httpx.Response(404, json={"message": f"no route for {path}"})

    def _object(self, method: str, object_type: str, object_id: str, body: Any) -> httpx.Response:
        record = self.objects[object_type].get(object_id)
        if record is None:
            return httpx.Response(404, json={"message": "not found"})
        if method == "PATCH":
            record.update(body["properties"])
            self.patches.append((object_type, object_id, body["properties"]))
        return httpx.Response(200, json={"id": object_id, "properties": dict(record)})

    def _create(self, object_type: str, body: Any) -> httpx.Response:
        props = body["properties"]
        email = props.get("email")
        for object_id, record in self.objects[object_type].items():
            if email and record.get("email") == email:
                return httpx.Response(
                    409, json={"message": f"Contact already exists. Existing ID: {object_id}"}
                )
        object_id = self.add(object_type, **props)
        return httpx.Response(201, json={"id": object_id, "properties": props})

    def _search(self, object_type: str, body: Any) -> httpx.Response:
        flt = body["filterGroups"][0]["filters"][0]
        results = [
            {"id": object_id, "properties": dict(record)}
            for object_id, record in self.objects[object_type].items()
            if record.get(flt["propertyName"]) == flt["value"]
        ]
        return httpx.Response(200, json={"total": len(results), "results": results})
