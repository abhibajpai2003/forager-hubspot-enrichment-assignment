from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.container import Services
from tests.fakes import FakeHubSpot


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "forager_mode": "mock",
            "forager_cache_dir": tmp_path / "cache",
            "hubspot_access_token": "test-token",
            "hubspot_client_secret": "test-secret",
            "admin_api_key": "admin-key",
            "webhook_delay_seconds": 0,
            "log_format": "console",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]

    return make


@pytest.fixture
def hubspot() -> FakeHubSpot:
    return FakeHubSpot()


@pytest.fixture
async def make_services(
    make_settings: Callable[..., Settings], hubspot: FakeHubSpot
) -> AsyncIterator[Callable[..., Services]]:
    created: list[Services] = []

    def make(**overrides: Any) -> Services:
        services = Services(make_settings(**overrides), hubspot_transport=hubspot.transport())
        created.append(services)
        return services

    yield make
    for services in created:
        await services.aclose()
