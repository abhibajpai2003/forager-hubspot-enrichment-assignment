"""Async client for the Forager API (https://docs.forager.ai).

Every paid call goes through the response cache first and the credit ledger second, so a
repeated lookup is free and the configured credit budget can never be exceeded.
"""

import asyncio
import json
from typing import Any

import httpx
import structlog

from app.cache import ResponseCache
from app.clients._http import send_with_retries
from app.credits import CREDIT_COST, CreditLedger
from app.models import Organization, PersonDetail, PersonRole

log = structlog.get_logger(__name__)

WORK_EMAIL_TERMINAL = frozenset({"completed", "failed"})


class ForagerError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def role_search_payload(
    *,
    organization_ids: list[int] | None = None,
    organization_domains: list[str] | None = None,
    role_title: str | None = None,
    person_name: str | None = None,
    person_linkedin_ids: list[str] | None = None,
    is_current: bool = True,
    page: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"page": page, "role_is_current": is_current}
    if organization_ids:
        payload["organizations"] = organization_ids
    if organization_domains:
        payload["organization_domains"] = organization_domains
    if role_title:
        payload["role_title"] = role_title
    if person_name:
        payload["person_name"] = person_name
    if person_linkedin_ids:
        payload["person_linkedin_public_identifiers"] = person_linkedin_ids
    return payload


def title_query(titles: list[str]) -> str:
    """Build a boolean `role_title` query. Forager needs multi-word phrases quoted."""
    cleaned = [t.replace('"', "").strip() for t in titles if t.strip()]
    return " OR ".join(f'"{t}"' for t in cleaned)


def pick_work_email(job: dict[str, Any]) -> str | None:
    """Return the best address from a finished work email job, or None if nothing was found.

    `emails` is documented as "every confirmed address, shortest first, each with its
    verdict"; entries may be plain strings, `[email, verdict]` pairs or objects, so all three
    shapes are accepted and `valid` verdicts are preferred.
    """
    if job.get("status") != "completed" or job.get("result") != "found":
        return None
    candidates: list[tuple[str, str]] = []
    for item in job.get("emails") or []:
        if isinstance(item, str):
            parts = item.split()
            address = parts[0] if parts else ""
            verdict = parts[1].strip("()") if len(parts) > 1 else "unknown"
        elif isinstance(item, list | tuple) and item:
            address = str(item[0])
            verdict = str(item[1]) if len(item) > 1 else "unknown"
        elif isinstance(item, dict):
            address = str(item.get("email") or "")
            verdict = str(item.get("validation_status") or item.get("verdict") or "unknown")
        else:
            continue
        if "@" in address:
            candidates.append((address.strip().lower(), verdict.lower()))
    if not candidates:
        return None
    for address, verdict in candidates:
        if verdict == "valid":
            return address
    return candidates[0][0]


class ForagerClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        account_id: int,
        ledger: CreditLedger,
        cache: ResponseCache,
        transport: httpx.AsyncBaseTransport | None = None,
        email_timeout: float = 180.0,
        poll_interval: float = 3.0,
        retry_base_wait: float = 0.5,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )
        self._account_id = account_id
        self._ledger = ledger
        self._cache = cache
        self._email_timeout = email_timeout
        self._poll_interval = poll_interval
        self._retry_base_wait = retry_base_wait

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def credits_remaining(self) -> int:
        return self._ledger.remaining

    def credit_summary(self) -> dict[str, Any]:
        return self._ledger.summary()

    # --- low level -----------------------------------------------------------------------

    def _path(self, suffix: str, *, v2: bool = False) -> str:
        prefix = "/api/v2" if v2 else "/api"
        return f"{prefix}/{self._account_id}/datastorage/{suffix}"

    async def _send(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        return await send_with_retries(
            self._http, method, path, json=payload, base_wait=self._retry_base_wait
        )

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> Any:
        """Decode a response. 404 means "no match" and becomes None."""
        if response.status_code == 404:
            return None
        if response.is_error:
            raise ForagerError(
                f"Forager {operation} failed: HTTP {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response.json()

    async def _paid_post(self, operation: str, path: str, payload: dict[str, Any]) -> Any:
        hit, cached = self._cache.get(operation, payload)
        if hit:
            log.info("forager_cache_hit", operation=operation)
            return cached
        cost = CREDIT_COST[operation]
        async with self._ledger.spend(operation, cost, detail=json.dumps(payload)[:200]):
            data = self._json(await self._send("POST", path, payload), operation)
        self._cache.set(operation, payload, data)
        return data

    # --- organizations -------------------------------------------------------------------

    async def organization_search(
        self, *, domains: list[str] | None = None, linkedin_ids: list[str] | None = None
    ) -> list[Organization]:
        payload: dict[str, Any] = {"page": 1}
        if domains:
            payload["domains"] = domains
        if linkedin_ids:
            payload["linkedin_public_identifiers"] = linkedin_ids
        data = await self._paid_post(
            "organization_search", self._path("organization_search/"), payload
        )
        return [Organization.model_validate(r) for r in (data or {}).get("search_results", [])]

    # --- people --------------------------------------------------------------------------

    async def person_role_search(self, payload: dict[str, Any]) -> list[PersonRole]:
        data = await self._paid_post(
            "person_role_search", self._path("person_role_search/"), payload
        )
        return [PersonRole.model_validate(r) for r in (data or {}).get("search_results", [])]

    async def person_role_search_totals(self, payload: dict[str, Any]) -> dict[str, int]:
        async with self._ledger.spend("totals", CREDIT_COST["totals"]):
            data = self._json(
                await self._send("POST", self._path("person_role_search/totals/"), payload),
                "person_role_search_totals",
            )
        return {k: int(v) for k, v in (data or {}).items() if isinstance(v, int)}

    async def reverse_lookup_by_email(self, email: str) -> PersonDetail | None:
        data = await self._paid_post(
            "reverse_email_lookup",
            self._path("person_detail_reverse_lookup/by_email/"),
            {"email": email.lower()},
        )
        return PersonDetail.model_validate(data) if data else None

    # --- contact details -----------------------------------------------------------------

    async def phone_numbers(
        self, *, person_id: int | None = None, linkedin_id: str | None = None
    ) -> list[str]:
        payload: dict[str, Any] = (
            {"person_id": person_id} if person_id else {"linkedin_public_identifier": linkedin_id}
        )
        data = await self._paid_post(
            "phone_numbers", self._path("person_contacts_lookup/phone_numbers/"), payload
        )
        return [str(p["phone_number"]) for p in data or [] if p.get("phone_number")]

    async def work_email(
        self,
        *,
        linkedin_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        domain: str | None = None,
    ) -> str | None:
        """Find a work email. Forager charges only when an address is found."""
        if linkedin_id:
            payload: dict[str, Any] = {"linkedin_public_identifier": linkedin_id}
        elif first_name and last_name and domain:
            payload = {"first_name": first_name, "last_name": last_name, "domain": domain}
        else:
            return None

        hit, cached = self._cache.get("work_email", payload)
        if hit:
            log.info("forager_cache_hit", operation="work_email")
            return self._email_from_job(cached)

        async with self._ledger.spend(
            "work_email", CREDIT_COST["work_email"], detail=json.dumps(payload)
        ) as charge:
            submitted = self._json(
                await self._send(
                    "POST", self._path("person_contacts_lookup/work_emails/", v2=True), payload
                ),
                "work_email",
            )
            if not submitted or "job_id" not in submitted:
                raise ForagerError(f"Forager work_email returned no job id: {submitted!r}")
            job = await self._wait_for_email_job(str(submitted["job_id"]))
            if job is None:
                # Still running server-side and may yet be charged, so keep the reservation.
                log.warning("forager_work_email_timeout", job_id=submitted["job_id"])
                return None
            if job.get("result") != "found":
                charge.cost = 0

        # The raw job is cached and parsed on read, so if parsing ever needs fixing, the
        # already-paid lookup is reused for free.
        self._cache.set("work_email", payload, job)
        return self._email_from_job(job)

    @staticmethod
    def _email_from_job(job: Any) -> str | None:
        if not isinstance(job, dict):
            return None
        email = pick_work_email(job)
        if email is None and job.get("status") == "completed" and job.get("result") == "found":
            # Forager charged for this, but we can't read the address. Stop rather than keep
            # paying for lookups we can't use.
            log.error("forager_work_email_unreadable", job=job)
            raise ForagerError(
                "Forager found a work email but its response format was not recognised; "
                "see the forager_work_email_unreadable log entry"
            )
        log.info("forager_work_email", found=email is not None, error=job.get("error_code"))
        return email

    async def _wait_for_email_job(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(f"person_contacts_lookup/work_emails/{job_id}/", v2=True)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._email_timeout
        while True:
            response = await self._send("GET", path)
            job = self._json(response, "work_email_status")
            if job and job.get("status") in WORK_EMAIL_TERMINAL:
                return dict(job)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            retry_after = response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else self._poll_interval
            await asyncio.sleep(min(delay, remaining))

    # --- account -------------------------------------------------------------------------

    async def accounts(self) -> list[dict[str, Any]]:
        """Accounts this API key can use, with their credit balances (this call is free)."""
        data = self._json(await self._send("GET", "/api/users/current/"), "users_current")
        return [
            {
                "id": account.get("id"),
                "name": account.get("name"),
                "credits_balance": (account.get("subscription") or {}).get("credits_balance"),
            }
            for account in (data or {}).get("accounts", [])
        ]

    async def credits_balance(self) -> str | None:
        """Live credit balance Forager reports for the configured account."""
        for account in await self.accounts():
            if account["id"] == self._account_id and account["credits_balance"] is not None:
                return str(account["credits_balance"])
        return None
