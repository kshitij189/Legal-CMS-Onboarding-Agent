from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.catalog import DataType, Entity, Transformation

SOURCE_KEY_PATTERN = r"^[A-Za-z0-9_.\-]{1,100}$"
SourceKey = Annotated[str, StringConstraints(pattern=SOURCE_KEY_PATTERN)]
CustomFieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,49}$")]
OptionValue = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SubmittedValue = str | int | float | bool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenRequest(StrictModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type name, not a secret
    expires_at: datetime


class TenantOut(BaseModel):
    slug: str
    name: str


class MeResponse(BaseModel):
    username: str
    display_name: str
    role: str
    tenant: TenantOut
    permissions: list[str]


class FieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    entity: Entity
    label: str
    description: str
    data_type: DataType
    required: bool
    enabled: bool
    is_custom: bool
    options: list[str] | None


class TransformationOut(BaseModel):
    name: Transformation
    applicable_types: list[DataType]


class SchemaResponse(BaseModel):
    tenant: TenantOut
    config_revision: int
    entities: list[Entity]
    data_types: list[DataType]
    transformations: list[TransformationOut]
    fields: list[FieldOut]


class FieldUpdate(StrictModel):
    label: Label | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    required: bool | None = None
    enabled: bool | None = None
    options: Annotated[list[OptionValue], Field(min_length=1, max_length=100)] | None = None


class CustomFieldCreate(StrictModel):
    entity: Entity
    name: CustomFieldName
    label: Label
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    data_type: DataType
    required: bool = False
    options: Annotated[list[OptionValue], Field(min_length=1, max_length=100)] | None = None


class MappingUpsert(StrictModel):
    target_field: str
    transformation: Transformation = Transformation.NONE
    value_map: dict[OptionValue, OptionValue] | None = None


class MappingOut(BaseModel):
    source_key: str
    target_field: str
    transformation: Transformation
    value_map: dict[str, str] | None
    updated_at: datetime


class FieldWriteResponse(BaseModel):
    field: FieldOut
    changed: bool
    config_revision: int


class MappingWriteResponse(BaseModel):
    mapping: MappingOut
    changed: bool
    config_revision: int


class MappingDeleteResponse(BaseModel):
    source_key: str
    config_revision: int


class ConfigSnapshot(BaseModel):
    tenant: TenantOut
    config_revision: int
    fields: list[FieldOut]
    intake_mappings: list[MappingOut]


class IntakeSubmission(StrictModel):
    values: dict[SourceKey, SubmittedValue]


class IntakeRecordOut(BaseModel):
    id: int
    values: dict[str, Any]
    unmapped_keys: list[str]
    config_revision: int
    submitted_by: str
    created_at: datetime
