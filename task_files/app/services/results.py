"""What each enrichment run reports back (logged for webhooks, returned by the admin API)."""

from pydantic import BaseModel, Field


class ContactResult(BaseModel):
    contact_id: str
    status: str  # enriched | skipped | not_found | error
    detail: str = ""
    match_method: str | None = None
    updated: list[str] = Field(default_factory=list)


class CandidateView(BaseModel):
    forager_person_id: int | None
    name: str | None
    title: str | None
    linkedin_url: str | None
    location: str | None


class CreatedContact(BaseModel):
    contact_id: str
    name: str | None
    title: str | None
    email: str | None
    created: bool  # False when an existing contact with the same email was updated


class SkippedCandidate(BaseModel):
    name: str | None
    reason: str


class FinderResult(BaseModel):
    company_id: str
    titles: list[str]
    preview: bool
    candidates: list[CandidateView] = Field(default_factory=list)
    created: list[CreatedContact] = Field(default_factory=list)
    skipped: list[SkippedCandidate] = Field(default_factory=list)
    stopped_reason: str | None = None


class CompanyResult(BaseModel):
    company_id: str
    status: str  # enriched | skipped | not_found | error
    detail: str = ""
    updated: list[str] = Field(default_factory=list)
    contacts: list[ContactResult] = Field(default_factory=list)
    finder: FinderResult | None = None
