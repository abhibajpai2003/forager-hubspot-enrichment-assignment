"""Builds the clients and services once per process, and runs webhook jobs."""

import asyncio

import httpx
import structlog

from app.cache import ResponseCache
from app.clients.forager import ForagerClient
from app.clients.forager_mock import mock_transport
from app.clients.hubspot import HubSpotClient
from app.config import Settings
from app.credits import CreditLedger
from app.services.company import CompanyEnricher
from app.services.contact import ContactEnricher
from app.services.finder import ContactFinder
from app.webhooks import EventDeduper, ObjectKind

log = structlog.get_logger(__name__)


class Services:
    def __init__(
        self,
        settings: Settings,
        *,
        forager_transport: httpx.AsyncBaseTransport | None = None,
        hubspot_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        live = settings.forager_mode == "live"
        cache_dir = settings.forager_cache_dir

        # Mock mode keeps its pretend spending in memory and never touches the real cache.
        ledger = CreditLedger(
            settings.forager_credit_budget,
            path=cache_dir / "credit_ledger.json" if live else None,
        )
        self.forager = ForagerClient(
            base_url=settings.forager_base_url,
            api_key=settings.forager_api_key.get_secret_value()
            if settings.forager_api_key
            else "mock",
            account_id=settings.forager_account_id or 0,
            ledger=ledger,
            cache=ResponseCache(cache_dir / "responses" if live else None),
            transport=forager_transport or (None if live else mock_transport()),
            email_timeout=settings.forager_email_timeout_seconds,
            poll_interval=3.0 if live else 0.0,
        )
        self.hubspot = HubSpotClient(
            access_token=settings.hubspot_access_token.get_secret_value()
            if settings.hubspot_access_token
            else "",
            base_url=settings.hubspot_base_url,
            transport=hubspot_transport,
        )
        self.contacts = ContactEnricher(self.hubspot, self.forager, settings)
        self.finder = ContactFinder(self.hubspot, self.forager, settings)
        self.companies = CompanyEnricher(
            self.hubspot, self.forager, self.contacts, self.finder, settings
        )
        self.deduper = EventDeduper()
        self._in_flight: set[str] = set()

    async def aclose(self) -> None:
        await self.forager.aclose()
        await self.hubspot.aclose()

    async def process_event(self, kind: ObjectKind, object_id: str) -> None:
        """Run one webhook job in the background.

        Waits WEBHOOK_DELAY_SECONDS first, so associations made right after a record is
        created (e.g. a contact added to a new company) are visible before we decide what to
        do. A second job for a record that is already being processed is dropped.
        """
        key = f"{kind}:{object_id}"
        if key in self._in_flight:
            log.info("webhook_job_already_running", kind=kind, object_id=object_id)
            return
        self._in_flight.add(key)
        try:
            await asyncio.sleep(self.settings.webhook_delay_seconds)
            if kind == "company":
                company_result = await self.companies.enrich(object_id)
                log.info("webhook_job_done", kind=kind, result=company_result.model_dump())
            else:
                contact_result = await self.contacts.enrich(object_id)
                log.info("webhook_job_done", kind=kind, result=contact_result.model_dump())
        except Exception:
            log.exception("webhook_job_failed", kind=kind, object_id=object_id)
        finally:
            self._in_flight.discard(key)
