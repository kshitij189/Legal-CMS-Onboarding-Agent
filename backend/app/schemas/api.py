from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.integrations.secrets import CREDENTIAL_REF_PATTERN
from app.permissions import Role
from app.schemas.normalized import NormalizedField, SchemaFormat, SchemaKind

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirmCreate(StrictModel):
    slug: Slug
    name: Name
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "UTC"


class FirmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    timezone: str
    is_active: bool


Username = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{2,63}$")]
Password = Annotated[str, StringConstraints(min_length=8, max_length=200)]


class UserCreate(StrictModel):
    username: Username
    display_name: Name
    password: Password
    role: Role


class SignupRequest(StrictModel):
    """Creates a firm and its first firm admin in one step."""

    class Admin(StrictModel):
        username: Username
        display_name: Name
        password: Password

    firm: FirmCreate
    admin: Admin


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: Role
    is_active: bool


class TokenRequest(StrictModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type name, not a secret
    expires_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    firm: FirmOut
    permissions: list[str]


class ConnectorCreate(StrictModel):
    name: Name
    connector_type: Literal["mock_cms_api"]
    base_url: Annotated[str, StringConstraints(pattern=r"^https?://[^\s/]+(/[^\s]*)?$")]
    credential_ref: Annotated[str, StringConstraints(pattern=CREDENTIAL_REF_PATTERN)]


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    name: str
    connector_type: str
    base_url: str
    credential_ref: str
    supported_operations: list[str]


class ProjectCreate(StrictModel):
    connector_id: int
    name: Name


class SchemaVersionSummary(BaseModel):
    id: int
    kind: SchemaKind
    version: int
    format: SchemaFormat
    title: str | None
    fingerprint: str
    field_count: int
    target_config_revision: int | None
    original_filename: str | None
    created_by: str | None
    created_at: datetime


class ProjectOut(BaseModel):
    id: int
    firm: FirmOut
    connector: ConnectorOut
    name: str
    created_by: str | None
    stage: Literal["awaiting_source_schema", "awaiting_target_inspection", "ready_for_mapping"]
    latest_source_schema: SchemaVersionSummary | None
    latest_target_schema: SchemaVersionSummary | None
    created_at: datetime


class SourceSchemaUpload(StrictModel):
    format: Literal["json_schema", "field_export_csv", "html_form"]
    filename: Annotated[str, StringConstraints(max_length=255)] | None = None
    content: Annotated[str, Field(min_length=1)]


class SchemaVersionOut(SchemaVersionSummary):
    fields: list[NormalizedField]
    supported_transformations: dict[str, list[str]] | None
    extensions: dict[str, object]


class SchemaRegistrationOut(BaseModel):
    """`created` is False when the content matched the latest version (idempotent re-submission)."""

    created: bool
    schema_version: SchemaVersionOut
    observed_config_revision: int | None = None
