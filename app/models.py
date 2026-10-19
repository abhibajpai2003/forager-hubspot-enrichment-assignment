"""Typed views of Forager responses and HubSpot webhook events.

Every model is lenient: unknown fields are ignored and `null` values fall back to the field
default, so a field Forager adds or leaves empty never breaks an enrichment run.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


# --- Forager ---------------------------------------------------------------------------


class LinkedinIndustry(_Lenient):
    id: int | None = None
    name: str | None = None


class LinkedinInfo(_Lenient):
    public_identifier: str | None = None
    public_profile_url: str | None = None
    industry: LinkedinIndustry | None = None


class OsmLocation(_Lenient):
    id: int | None = None
    name: str | None = None
    place_type: str | None = None


class Location(_Lenient):
    id: int | None = None
    name: str | None = None
    osm_locations: list[OsmLocation] = Field(default_factory=list)


class Address(_Lenient):
    street_number: str | None = None
    street_name: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None
    summary: str | None = None


class FinanceInfo(_Lenient):
    revenue: int | None = None


class Keyword(_Lenient):
    id: int | None = None
    name: str | None = None


class Organization(_Lenient):
    id: int | None = None
    name: str | None = None
    legal_name: str | None = None
    website: str | None = None
    domain: str | None = None
    logo: str | None = None
    description: str | None = None
    founded_date: str | None = None
    operating_status: str | None = None
    employees_range: str | None = None
    employees_amount: int | None = None
    keywords: list[Keyword] = Field(default_factory=list)
    location: Location | None = None
    finance_info: FinanceInfo | None = None
    linkedin_info: LinkedinInfo | None = None
    addresses: list[Address] = Field(default_factory=list)


class Skill(_Lenient):
    name: str | None = None


class Person(_Lenient):
    id: int | None = None
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    photo: str | None = None
    headline: str | None = None
    description: str | None = None
    skills: list[Skill] = Field(default_factory=list)
    location: Location | None = None
    linkedin_info: LinkedinInfo | None = None


class PersonRole(_Lenient):
    """One row of a person role search: a person's role at an organization."""

    id: int | None = None
    role_title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    duration: str | None = None
    description: str | None = None
    is_current: bool | None = None
    organization: Organization | None = None
    organization_name: str | None = None
    person: Person | None = None


class PersonDetail(Person):
    """Response of the person detail and reverse lookups: a person plus their roles."""

    roles: list[PersonRole] = Field(default_factory=list)

    def current_role(self) -> PersonRole | None:
        current = [r for r in self.roles if r.is_current]
        if not current:
            return None
        return max(current, key=lambda r: r.start_date or "")


# --- HubSpot ---------------------------------------------------------------------------


class HubSpotEvent(_Lenient):
    """One event in a HubSpot webhook batch. Field names match HubSpot's payload."""

    eventId: int | None = None
    subscriptionId: int | None = None
    portalId: int | None = None
    occurredAt: int | None = None
    subscriptionType: str | None = None
    objectTypeId: str | None = None
    objectId: int | None = None
    changeSource: str | None = None
    attemptNumber: int | None = None
