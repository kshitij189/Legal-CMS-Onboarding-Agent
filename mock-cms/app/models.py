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

from app.catalog import DataType, Entity, Transformation
from app.db import Base
from app.permissions import Role


def utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: type[Any]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Tenant(Base):
    """A firm workspace. All configuration and records are scoped to a tenant."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # Incremented on every configuration change, so readers can detect drift.
    config_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", Role), name="ck_users_role"),
        # Target of composite FKs that keep cross-table references inside one tenant.
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tenant: Mapped[Tenant] = relationship(lazy="joined")


class AuthSession(Base):
    """API bearer tokens and browser sessions. Only a hash of the token is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (CheckConstraint("kind IN ('api', 'browser')", name="ck_auth_sessions_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_field_definitions_tenant_key"),
        UniqueConstraint("tenant_id", "id", name="uq_field_definitions_tenant_id_id"),
        CheckConstraint(_in("entity", Entity), name="ck_field_definitions_entity"),
        CheckConstraint(_in("data_type", DataType), name="ck_field_definitions_data_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    entity: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    # none_as_null: store an absent value as SQL NULL, not JSON 'null'.
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IntakeMapping(Base):
    """Maps an external intake-form key to a CMS field, with an optional transformation."""

    __tablename__ = "intake_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", name="uq_intake_mappings_tenant_source"),
        UniqueConstraint("tenant_id", "target_field_id", name="uq_intake_mappings_tenant_target"),
        ForeignKeyConstraint(
            ["tenant_id", "target_field_id"],
            ["field_definitions.tenant_id", "field_definitions.id"],
            name="fk_intake_mappings_target_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            _in("transformation", Transformation), name="ck_intake_mappings_transformation"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(100))
    target_field_id: Mapped[int] = mapped_column(Integer)
    transformation: Mapped[str] = mapped_column(String(32), default=Transformation.NONE.value)
    value_map: Mapped[dict[str, str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    target_field: Mapped[FieldDefinition] = relationship(lazy="joined")


class IntakeRecord(Base):
    """An accepted intake submission. Values are keyed by CMS field key."""

    __tablename__ = "intake_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "submitted_by_id"],
            ["users.tenant_id", "users.id"],
            name="fk_intake_records_submitter_same_tenant",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    unmapped_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    config_revision: Mapped[int] = mapped_column(Integer)
    submitted_by_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submitted_by: Mapped[User] = relationship(lazy="joined")
