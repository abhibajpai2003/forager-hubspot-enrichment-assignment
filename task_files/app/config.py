"""Runtime configuration, read from environment variables (and a local `.env` file)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Forager
    forager_api_key: SecretStr | None = None
    forager_account_id: int | None = None
    forager_base_url: str = "https://api-v2.forager.ai"
    forager_mode: Literal["mock", "live"] = "mock"
    forager_credit_budget: int = Field(default=45, ge=0)
    forager_cache_dir: Path = Path(".forager_cache")
    forager_email_timeout_seconds: float = Field(default=180.0, gt=0)
    enable_phone_lookup: bool = False

    # HubSpot
    hubspot_access_token: SecretStr | None = None
    hubspot_client_secret: SecretStr | None = None
    hubspot_base_url: str = "https://api.hubapi.com"
    public_base_url: str | None = None

    # Enrichment behaviour
    default_job_titles: str = (
        "Account Executive,Recruiter,Solutions Engineer,Partnerships,Customer Success"
    )
    max_contacts_per_company: int = Field(default=5, ge=1, le=25)
    webhook_delay_seconds: float = Field(default=10.0, ge=0)
    enrichment_overwrite: bool = False

    # Service
    admin_api_key: SecretStr | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @model_validator(mode="after")
    def _require_live_credentials(self) -> "Settings":
        if self.forager_mode == "live" and (
            self.forager_api_key is None or self.forager_account_id is None
        ):
            raise ValueError("FORAGER_MODE=live needs FORAGER_API_KEY and FORAGER_ACCOUNT_ID")
        return self

    @property
    def job_titles(self) -> list[str]:
        return [t.strip() for t in self.default_job_titles.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
