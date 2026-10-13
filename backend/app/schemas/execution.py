"""Typed execution actions and CMS configuration snapshots (blueprint §5 steps 8–10)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActionType(StrEnum):
    CREATE_CUSTOM_FIELD = "create_custom_field"
    UPSERT_INTAKE_MAPPING = "upsert_intake_mapping"


class Channel(StrEnum):
    API = "api"
    BROWSER = "browser"


class CustomFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str
    name: str
    label: str
    data_type: str
    required: bool
    options: list[str] | None = None

    @property
    def key(self) -> str:
        return f"{self.entity}.custom.{self.name}"


class MappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str
    target_field: str
    transformation: str
    value_map: dict[str, str] | None = None


class PlannedAction(BaseModel):
    """One step of an execution plan, derived only from an approved mapping version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    action_type: ActionType
    subject: str  # custom field key or mapping source key
    custom_field: CustomFieldSpec | None = None
    mapping: MappingSpec | None = None


class CMSField(BaseModel):
    key: str
    entity: str
    label: str
    data_type: str
    required: bool
    enabled: bool
    is_custom: bool
    options: list[str] | None


class CMSConfiguration(BaseModel):
    """What the CMS actually stores, read back through its API."""

    config_revision: int
    fields: list[CMSField]
    mappings: list[MappingSpec]

    def field(self, key: str) -> CMSField | None:
        return next((f for f in self.fields if f.key == key), None)

    def mapping(self, source_key: str) -> MappingSpec | None:
        return next((m for m in self.mappings if m.source_key == source_key), None)


class WriteOutcome(BaseModel):
    changed: bool | None  # None when the channel cannot tell (e.g. browser without a notice)
    evidence: dict[str, Any]


class DriftIssue(BaseModel):
    field: str
    code: str
    message: str
    blocking: bool


class VerificationCheck(BaseModel):
    subject: str
    check_type: str  # custom_field | mapping
    expected: dict[str, Any]
    actual: dict[str, Any] | None
    status: str  # match | mismatch | missing
    reason: str
