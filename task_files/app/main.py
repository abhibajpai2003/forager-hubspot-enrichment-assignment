"""FastAPI app: the HubSpot webhook endpoint plus admin endpoints for manual runs.

Run with: uvicorn app.main:create_app --factory
"""

import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.clients.forager import ForagerError
from app.clients.hubspot import HubSpotError
from app.config import Settings, get_settings
from app.container import Services
from app.logs import configure_logging
from app.mapping import normalize_domain
from app.services.results import CompanyResult, ContactResult, FinderResult
from app.webhooks import event_kind, parse_events, request_uri, verify_signature

log = structlog.get_logger(__name__)


class FindContactsRequest(BaseModel):
    titles: list[str] | None = Field(
        default=None, description="Job titles to search for; defaults to DEFAULT_JOB_TITLES."
    )
    limit: int | None = Field(default=None, ge=1, le=25)
    preview: bool = Field(
        default=False,
        description="Only search (1 credit): list matching people without buying emails "
        "or writing to HubSpot.",
    )


def create_app(
    settings: Settings | None = None,
    *,
    forager_transport: httpx.AsyncBaseTransport | None = None,
    hubspot_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = Services(
            settings, forager_transport=forager_transport, hubspot_transport=hubspot_transport
        )
        app.state.services = services
        log.info(
            "startup",
            forager_mode=settings.forager_mode,
            credit_budget=settings.forager_credit_budget,
            credits_remaining=services.forager.credits_remaining,
            phone_lookup=settings.enable_phone_lookup,
            hubspot_token_set=settings.hubspot_access_token is not None,
            webhook_secret_set=settings.hubspot_client_secret is not None,
        )
        if settings.forager_mode == "mock":
            log.warning("forager_mock_mode", detail="Forager data is fake; set FORAGER_MODE=live")
        yield
        await services.aclose()

    app = FastAPI(
        title="Forager-HubSpot Enrichment",
        version="0.1.0",
        description="Enriches HubSpot companies and contacts with Forager data.",
        lifespan=lifespan,
    )

    def get_services(request: Request) -> Services:
        services: Services = request.app.state.services
        return services

    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        expected = settings.admin_api_key
        if expected is None:
            raise HTTPException(503, "ADMIN_API_KEY is not configured")
        if not x_admin_key or not hmac.compare_digest(x_admin_key, expected.get_secret_value()):
            raise HTTPException(401, "missing or wrong X-Admin-Key header")

    ServicesDep = Annotated[Services, Depends(get_services)]
    admin = [Depends(require_admin)]

    @app.exception_handler(HubSpotError)
    async def hubspot_error(_: Request, exc: HubSpotError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ForagerError)
    async def forager_error(_: Request, exc: ForagerError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "forager_mode": settings.forager_mode}

    @app.post("/webhooks/hubspot")
    async def hubspot_webhook(
        request: Request, background: BackgroundTasks, services: ServicesDep
    ) -> dict[str, int]:
        """Receives HubSpot's batched webhook events, verifies them, and queues the work.

        Replies right away (HubSpot expects an answer within seconds); enrichment runs in the
        background.
        """
        secret = settings.hubspot_client_secret
        if secret is None:
            log.error("webhook_secret_missing")
            raise HTTPException(503, "HUBSPOT_CLIENT_SECRET is not configured")
        body = await request.body()
        uri = request_uri(request, settings.public_base_url)
        if not verify_signature(
            secret=secret.get_secret_value(),
            method=request.method,
            uri=uri,
            body=body,
            headers=request.headers,
        ):
            log.warning("webhook_rejected", reason="bad signature", uri=uri)
            raise HTTPException(401, "invalid signature")
        try:
            events = parse_events(json.loads(body))
        except ValueError as exc:
            raise HTTPException(400, "body is not valid JSON") from exc

        scheduled = 0
        for event in events:
            kind = event_kind(event)
            if kind is None or event.objectId is None:
                continue
            key = (
                f"event:{event.eventId}"
                if event.eventId is not None
                else f"{kind}:{event.objectId}:{event.occurredAt}"
            )
            if services.deduper.seen_before(key):
                log.info("webhook_duplicate", kind=kind, object_id=event.objectId)
                continue
            background.add_task(services.process_event, kind, str(event.objectId))
            scheduled += 1
        log.info("webhook_received", events=len(events), scheduled=scheduled)
        return {"received": len(events), "scheduled": scheduled}

    @app.post("/enrich/company/{company_id}", dependencies=admin)
    async def enrich_company(
        company_id: str, services: ServicesDep, force: bool = False
    ) -> CompanyResult:
        """Run the full company flow now: enrich, then enrich or find its contacts."""
        return await services.companies.enrich(company_id, force=force)

    @app.post("/enrich/contact/{contact_id}", dependencies=admin)
    async def enrich_contact(
        contact_id: str, services: ServicesDep, force: bool = False
    ) -> ContactResult:
        return await services.contacts.enrich(contact_id, force=force)

    @app.post("/companies/{company_id}/find-contacts", dependencies=admin)
    async def find_contacts(
        company_id: str, body: FindContactsRequest, services: ServicesDep
    ) -> FinderResult:
        """Find people at a company by job title. Use `preview` to only list matches."""
        company = await services.hubspot.get_object(
            "companies", company_id, ["domain", "website", "forager_org_id"]
        )
        if company is None:
            raise HTTPException(404, "company not found")
        props: dict[str, Any] = company.get("properties") or {}
        domain = normalize_domain(props.get("domain") or props.get("website"))
        org_id_text = str(props.get("forager_org_id") or "")
        org_id = int(org_id_text) if org_id_text.isdigit() else None
        if not domain and org_id is None:
            raise HTTPException(422, "company has no domain; set one or enrich it first")
        return await services.finder.find(
            company_id=company_id,
            titles=body.titles or settings.job_titles,
            limit=body.limit or settings.max_contacts_per_company,
            domain=domain,
            org_id=org_id,
            preview=body.preview,
        )

    @app.get("/credits", dependencies=admin)
    async def credits(services: ServicesDep) -> dict[str, Any]:
        """Credits spent by this service, plus the live balance Forager reports."""
        summary = services.forager.credit_summary()
        summary["mode"] = settings.forager_mode
        if settings.forager_mode == "live":
            try:
                summary["forager_balance"] = await services.forager.credits_balance()
            except ForagerError as exc:
                summary["forager_balance_error"] = str(exc)
        return summary

    return app
