"""Persistence models (blueprint §11). Phase 2 tables: firms, cms_connectors, onboarding_projects,
schema_definitions, field_definitions.

Tenant integrity: every child row carries firm_id, and composite foreign keys on (firm_id, id) make
it impossible to attach a project to another firm's connector, or a schema to another firm's
project.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.schemas.normalized import (
    DataType,
    NormalizedField,
    NormalizedSchema,
    SchemaFormat,
    SchemaKind,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: Any) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


CONNECTOR_TYPES = ("mock_cms_api",)


class Firm(Base):
    __tablename__ = "firms"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CMSConnector(Base):
    """Where and how to reach a firm's target CMS. Holds a secret reference, never the secret."""

    __tablename__ = "cms_connectors"
    __table_args__ = (
        UniqueConstraint("firm_id", "name", name="uq_cms_connectors_firm_name"),
        UniqueConstraint("firm_id", "id", name="uq_cms_connectors_firm_id_id"),
        CheckConstraint(
            f"connector_type IN ({', '.join(repr(t) for t in CONNECTOR_TYPES)})",
            name="ck_cms_connectors_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    connector_type: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(500))
    credential_ref: Mapped[str] = mapped_column(String(100))
    supported_operations: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OnboardingProject(Base):
    __tablename__ = "onboarding_projects"
    __table_args__ = (
        UniqueConstraint("firm_id", "id", name="uq_onboarding_projects_firm_id_id"),
        ForeignKeyConstraint(
            ["firm_id", "connector_id"],
            ["cms_connectors.firm_id", "cms_connectors.id"],
            name="fk_onboarding_projects_connector_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    firm: Mapped[Firm] = relationship(lazy="joined")
    connector: Mapped[CMSConnector] = relationship(lazy="joined", viewonly=True)


class SchemaDefinition(Base):
    """One immutable, versioned source or target schema of a project."""

    __tablename__ = "schema_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "version", name="uq_schema_definitions_version"),
        CheckConstraint(_in("kind", SchemaKind), name="ck_schema_definitions_kind"),
        CheckConstraint(_in("format", SchemaFormat), name="ck_schema_definitions_format"),
        CheckConstraint("version >= 1", name="ck_schema_definitions_version_positive"),
        ForeignKeyConstraint(
            ["firm_id", "project_id"],
            ["onboarding_projects.firm_id", "onboarding_projects.id"],
            name="fk_schema_definitions_project_same_firm",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    extensions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    supported_transformations: Mapped[dict[str, list[str]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    # Source schemas: where the uploaded document was archived, and its client-supplied name
    # (metadata only, never used to build a path).
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Target schemas: the CMS configuration revision observed when this version was captured.
    target_config_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fields: Mapped[list["FieldDefinition"]] = relationship(
        order_by="FieldDefinition.position", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_normalized(self) -> NormalizedSchema:
        return NormalizedSchema(
            kind=SchemaKind(self.kind),
            format=SchemaFormat(self.format),
            title=self.title,
            fields=[f.to_normalized() for f in self.fields],
            supported_transformations=(
                {k: [DataType(t) for t in v] for k, v in self.supported_transformations.items()}
                if self.supported_transformations is not None
                else None
            ),
            extensions=self.extensions,
        )


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    __table_args__ = (
        UniqueConstraint("schema_definition_id", "key", name="uq_field_definitions_schema_key"),
        CheckConstraint(_in("data_type", DataType), name="ck_field_definitions_data_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_definition_id: Mapped[int] = mapped_column(
        ForeignKey("schema_definitions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean)
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extensions: Mapped[dict[str, Any]] = mapped_column(JSONB)

    @classmethod
    def from_normalized(cls, position: int, field: NormalizedField) -> "FieldDefinition":
        return cls(
            position=position,
            key=field.key,
            label=field.label,
            description=field.description,
            data_type=field.data_type.value,
            required=field.required,
            options=field.options,
            max_length=field.max_length,
            entity=field.entity,
            canonical_ref=field.canonical_ref,
            extensions=field.extensions,
        )

    def to_normalized(self) -> NormalizedField:
        return NormalizedField(
            key=self.key,
            label=self.label,
            description=self.description,
            data_type=DataType(self.data_type),
            required=self.required,
            options=self.options,
            max_length=self.max_length,
            entity=self.entity,
            canonical_ref=self.canonical_ref,
            extensions=self.extensions,
        )
