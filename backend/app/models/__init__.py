"""Persistence models (blueprint §11): firms, users, auth_tokens, cms_connectors,
onboarding_projects, schema_definitions, field_definitions.

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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.permissions import Role
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


class User(Base):
    """A reviewer account (decision D5). Belongs to exactly one firm."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", Role), name="ck_users_role"),
        UniqueConstraint("firm_id", "id", name="uq_users_firm_id_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    firm: Mapped[Firm] = relationship(lazy="joined")


class AuthToken(Base):
    """Bearer tokens. Only the SHA-256 hash is stored."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


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
        ForeignKeyConstraint(
            ["firm_id", "created_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_onboarding_projects_creator_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    # Null only for rows created before authentication existed (migration 0002).
    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    firm: Mapped[Firm] = relationship(lazy="joined")
    connector: Mapped[CMSConnector] = relationship(lazy="joined", viewonly=True)
    created_by: Mapped[User | None] = relationship(lazy="joined", viewonly=True)


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
        ForeignKeyConstraint(
            ["firm_id", "created_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_schema_definitions_creator_same_firm",
            ondelete="RESTRICT",
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
    # Who uploaded / inspected. Null only for rows created before authentication existed.
    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fields: Mapped[list["FieldDefinition"]] = relationship(
        order_by="FieldDefinition.position", cascade="all, delete-orphan", lazy="selectin"
    )
    created_by: Mapped[User | None] = relationship(lazy="joined", viewonly=True)

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


# --- Phase 3: proposals, review, approval, audit ------------------------------------------------

GENERATION_STATUSES = ("succeeded", "failed")
# "system": placeholder for a source field the model skipped, so a human must decide it.
PROPOSAL_ORIGINS = ("llm", "reviewer", "system")
REVIEW_STATUSES = ("pending", "approved", "rejected", "edited", "needs_clarification")
VALIDATION_STATUSES = ("valid", "needs_review", "invalid")


def _in_values(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProposalGeneration(Base):
    """One LLM proposal run for a pinned pair of source and target schema versions."""

    __tablename__ = "proposal_generations"
    __table_args__ = (
        UniqueConstraint("firm_id", "id", name="uq_proposal_generations_firm_id_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_proposal_generations_idem"),
        CheckConstraint(
            _in_values("status", GENERATION_STATUSES), name="ck_proposal_generations_status"
        ),
        ForeignKeyConstraint(
            ["firm_id", "project_id"],
            ["onboarding_projects.firm_id", "onboarding_projects.id"],
            name="fk_proposal_generations_project_same_firm",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["firm_id", "created_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_proposal_generations_creator_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    source_schema_id: Mapped[int] = mapped_column(
        ForeignKey("schema_definitions.id", ondelete="RESTRICT")
    )
    target_schema_id: Mapped[int] = mapped_column(
        ForeignKey("schema_definitions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Providers that failed before the one that answered: [[provider, error_category], ...]
    fallbacks: Mapped[list[list[str]]] = mapped_column(JSONB, default=list)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    unproposed_source_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    untrusted_source_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    proposals: Mapped[list["MappingProposal"]] = relationship(
        order_by="MappingProposal.id", lazy="selectin", viewonly=True
    )
    created_by: Mapped[User] = relationship(lazy="joined", viewonly=True)


class MappingProposal(Base):
    """A proposed mapping for one source field. `content` is immutable once written; a reviewer's
    edit is stored as a new row (origin "reviewer") that supersedes the original."""

    __tablename__ = "mapping_proposals"
    __table_args__ = (
        CheckConstraint(_in_values("origin", PROPOSAL_ORIGINS), name="ck_mapping_proposals_origin"),
        CheckConstraint(
            _in_values("review_status", REVIEW_STATUSES), name="ck_mapping_proposals_review"
        ),
        CheckConstraint(
            _in_values("validation_status", VALIDATION_STATUSES),
            name="ck_mapping_proposals_validation",
        ),
        ForeignKeyConstraint(
            ["firm_id", "generation_id"],
            ["proposal_generations.firm_id", "proposal_generations.id"],
            name="fk_mapping_proposals_generation_same_firm",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["firm_id", "reviewed_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_mapping_proposals_reviewer_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    generation_id: Mapped[int] = mapped_column(Integer, index=True)
    origin: Mapped[str] = mapped_column(String(16))
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("mapping_proposals.id", ondelete="RESTRICT"), nullable=True
    )
    source_field: Mapped[str] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(String(32))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(String(16))
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    review_status: Mapped[str] = mapped_column(String(32), default="pending")
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reviewed_by: Mapped[User | None] = relationship(lazy="joined", viewonly=True)


class MappingVersion(Base):
    """An approved, immutable configuration plan (blueprint §14 "approval refers to a specific
    mapping version"). A database trigger rejects changes to its content after insert."""

    __tablename__ = "mapping_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_mapping_versions_version"),
        UniqueConstraint("generation_id", "fingerprint", name="uq_mapping_versions_generation_fp"),
        CheckConstraint("status IN ('approved', 'superseded')", name="ck_mapping_versions_status"),
        ForeignKeyConstraint(
            ["firm_id", "project_id"],
            ["onboarding_projects.firm_id", "onboarding_projects.id"],
            name="fk_mapping_versions_project_same_firm",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["firm_id", "approved_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_mapping_versions_approver_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("proposal_generations.id", ondelete="RESTRICT")
    )
    source_schema_id: Mapped[int] = mapped_column(
        ForeignKey("schema_definitions.id", ondelete="RESTRICT")
    )
    target_schema_id: Mapped[int] = mapped_column(
        ForeignKey("schema_definitions.id", ondelete="RESTRICT")
    )
    # [{"proposal_id": int, "mapping": ProposedMapping-as-dict}, ...] for approved map and
    # requires_custom_field decisions.
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    excluded: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    coverage_gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    complete: Mapped[bool] = mapped_column(Boolean)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="approved")
    approved_by_id: Mapped[int] = mapped_column(Integer)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    approved_by: Mapped[User] = relationship(lazy="joined", viewonly=True)


class AuditEvent(Base):
    """Append-only history of who did what (blueprint §11). Updates are rejected by a trigger."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actor: Mapped[User | None] = relationship(lazy="joined", viewonly=True)


# --- Phase 4: execution runs, steps and read-after-write verification ---------------------------

RUN_STATUSES = ("running", "succeeded", "failed", "blocked", "verification_failed")
STEP_STATUSES = ("pending", "applied", "already_applied", "failed", "skipped")


class OnboardingRun(Base):
    """One execution of an approved mapping version against the firm's CMS."""

    __tablename__ = "onboarding_runs"
    __table_args__ = (
        UniqueConstraint("firm_id", "id", name="uq_onboarding_runs_firm_id_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_onboarding_runs_idem"),
        CheckConstraint(_in_values("status", RUN_STATUSES), name="ck_onboarding_runs_status"),
        CheckConstraint("channel IN ('api', 'browser')", name="ck_onboarding_runs_channel"),
        # At most one run per project may be active, so two runs never write concurrently.
        Index(
            "uq_onboarding_runs_one_active",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        ForeignKeyConstraint(
            ["firm_id", "project_id"],
            ["onboarding_projects.firm_id", "onboarding_projects.id"],
            name="fk_onboarding_runs_project_same_firm",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["firm_id", "created_by_id"],
            ["users.firm_id", "users.id"],
            name="fk_onboarding_runs_creator_same_firm",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    mapping_version_id: Mapped[int] = mapped_column(
        ForeignKey("mapping_versions.id", ondelete="RESTRICT")
    )
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    drift_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    config_revision_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_revision_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Relative to the artifacts dir; holds browser traces/screenshots of failed actions.
    artifact_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["ActionStep"]] = relationship(
        order_by="ActionStep.sequence", lazy="selectin", viewonly=True
    )
    verification: Mapped[list["VerificationResult"]] = relationship(
        order_by="VerificationResult.id", lazy="selectin", viewonly=True
    )
    created_by: Mapped[User] = relationship(lazy="joined", viewonly=True)


class ActionStep(Base):
    """A planned and executed action. Payloads are configuration, never client data."""

    __tablename__ = "action_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_action_steps_run_sequence"),
        CheckConstraint(_in_values("status", STEP_STATUSES), name="ck_action_steps_status"),
        ForeignKeyConstraint(
            ["firm_id", "run_id"],
            ["onboarding_runs.firm_id", "onboarding_runs.id"],
            name="fk_action_steps_run_same_firm",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationResult(Base):
    """Expected vs actual for one planned action, read back from the CMS after execution."""

    __tablename__ = "verification_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('match', 'mismatch', 'missing')", name="ck_verification_results_status"
        ),
        ForeignKeyConstraint(
            ["firm_id", "run_id"],
            ["onboarding_runs.firm_id", "onboarding_runs.id"],
            name="fk_verification_results_run_same_firm",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    subject: Mapped[str] = mapped_column(String(200))
    check_type: Mapped[str] = mapped_column(String(32))
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
