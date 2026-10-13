"""Typed shapes for mapping proposals (blueprint §4 capability 3, §6).

`ProposeFieldMappingsArgs` is what the LLM must fill in via the `propose_field_mappings` tool. It is
deliberately flat and null-free so every provider's tool schema dialect can express it.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Decision(StrEnum):
    MAP = "map"
    AMBIGUOUS = "ambiguous"
    NO_TARGET = "no_target"
    UNSUPPORTED = "unsupported"
    REQUIRES_CUSTOM_FIELD = "requires_custom_field"


class ValueMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_value: Annotated[str, StringConstraints(max_length=200)]
    target_value: Annotated[str, StringConstraints(max_length=200)]


class ProposedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: Annotated[str, StringConstraints(min_length=1, max_length=200)] = Field(
        description="Key of the source field, exactly as listed."
    )
    decision: Decision = Field(
        description="map | ambiguous | no_target | unsupported | requires_custom_field"
    )
    target_field: Annotated[str, StringConstraints(max_length=200)] = Field(
        default="", description="Target field key for decision=map, else empty."
    )
    transformation: Annotated[str, StringConstraints(max_length=50)] = Field(
        default="none", description="One of the listed supported transformations."
    )
    value_map: list[ValueMapEntry] = Field(
        default_factory=list,
        description="For enum targets: one entry per source option -> a listed target option.",
    )
    candidates: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, description="For decision=ambiguous: plausible target field keys."
    )
    custom_field_entity: Annotated[str, StringConstraints(max_length=50)] = Field(
        default="", description="For requires_custom_field: entity of the field to create."
    )
    custom_field_name: Annotated[str, StringConstraints(max_length=50)] = Field(
        default="", description="For requires_custom_field: snake_case name of the new field."
    )
    custom_field_data_type: Annotated[str, StringConstraints(max_length=50)] = Field(
        default="", description="For requires_custom_field: data type of the new field."
    )
    custom_field_options: list[Annotated[str, StringConstraints(max_length=100)]] = Field(
        default_factory=list, description="For requires_custom_field with enum type: options."
    )
    confidence: float = Field(ge=0, le=1, description="0..1. Not proof of correctness.")
    rationale: Annotated[str, StringConstraints(max_length=1000)] = Field(
        description="Why, citing only the provided field definitions."
    )
    open_questions: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, description="Questions a human must answer, if any."
    )


class ProposeFieldMappingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[ProposedMapping] = Field(
        description="Exactly one entry per source field.", max_length=500
    )


class ValidationStatus(StrEnum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"
    INVALID = "invalid"


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: ValidationStatus  # needs_review or invalid


class MappingValidation(BaseModel):
    source_field: str
    status: ValidationStatus
    issues: list[ValidationIssue]


class CoverageGap(BaseModel):
    target_field: str
    code: str
    message: str


class ValidationReport(BaseModel):
    mappings: list[MappingValidation]
    coverage_gaps: list[CoverageGap]
    # Source fields the model skipped; each needs a human decision.
    unproposed_source_fields: list[str]
