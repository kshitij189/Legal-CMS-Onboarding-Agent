from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.mapping import ProposedMapping


class ProposalOut(BaseModel):
    id: int
    origin: str
    supersedes_id: int | None
    source_field: str
    decision: str
    mapping: ProposedMapping
    validation_status: str
    validation_issues: list[dict[str, Any]]
    review_status: str
    review_comment: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class GenerationOut(BaseModel):
    id: int
    status: str
    created: bool
    provider: str | None
    model: str | None
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    fallbacks: list[list[str]]
    source_schema_id: int
    target_schema_id: int
    coverage_gaps: list[dict[str, Any]]
    unproposed_source_fields: list[str]
    untrusted_source_fields: list[str]
    created_by: str
    created_at: datetime
    proposals: list[ProposalOut]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "request_clarification", "edit"]
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    mapping: ProposedMapping | None = None


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: int


class MappingVersionOut(BaseModel):
    id: int
    version: int
    status: str
    created: bool
    generation_id: int
    source_schema_id: int
    target_schema_id: int
    fingerprint: str
    complete: bool
    items: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    coverage_gaps: list[dict[str, Any]]
    approved_by: str
    approved_at: datetime


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    entity_type: str
    entity_id: int | None
    actor: str | None
    details: dict[str, Any]
    created_at: datetime
