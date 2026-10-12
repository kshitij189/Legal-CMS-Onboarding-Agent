"""Firms, connectors, onboarding projects and versioned schema definitions.

Schema versions are immutable. Registering content whose fingerprint equals the latest version of
the same kind returns that version instead of creating a new one, so retries are idempotent.
"""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.schema_normalizer import normalize_source
from app.errors import Conflict, NotFound
from app.integrations.base import ensure_allowed_origin
from app.integrations.registry import SUPPORTED_OPERATIONS, AdapterFactory
from app.models import (
    CMSConnector,
    FieldDefinition,
    Firm,
    OnboardingProject,
    SchemaDefinition,
)
from app.schemas.api import ConnectorCreate, FirmCreate, ProjectCreate
from app.schemas.normalized import NormalizedSchema, SchemaFormat, SchemaKind

_EXTENSIONS = {
    SchemaFormat.JSON_SCHEMA: "json",
    SchemaFormat.FIELD_EXPORT_CSV: "csv",
    SchemaFormat.HTML_FORM: "html",
}


def _commit_or_conflict(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(message) from None


# --- firms and connectors -----------------------------------------------------------------------


def create_firm(db: Session, payload: FirmCreate) -> Firm:
    firm = Firm(slug=payload.slug, name=payload.name, timezone=payload.timezone)
    db.add(firm)
    _commit_or_conflict(db, f"Firm '{payload.slug}' already exists.")
    return firm


def list_firms(db: Session) -> list[Firm]:
    return list(db.scalars(select(Firm).order_by(Firm.id)))


def get_firm(db: Session, firm_id: int) -> Firm:
    firm = db.get(Firm, firm_id)
    if firm is None:
        raise NotFound(f"Firm {firm_id} not found.")
    return firm


def create_connector(db: Session, firm_id: int, payload: ConnectorCreate) -> CMSConnector:
    get_firm(db, firm_id)
    ensure_allowed_origin(payload.base_url, get_settings().allowed_cms_origins)
    connector = CMSConnector(
        firm_id=firm_id,
        name=payload.name,
        connector_type=payload.connector_type,
        base_url=payload.base_url.rstrip("/"),
        credential_ref=payload.credential_ref,
        supported_operations=SUPPORTED_OPERATIONS[payload.connector_type],
    )
    db.add(connector)
    _commit_or_conflict(db, f"Connector '{payload.name}' already exists for this firm.")
    return connector


def list_connectors(db: Session, firm_id: int) -> list[CMSConnector]:
    get_firm(db, firm_id)
    return list(
        db.scalars(
            select(CMSConnector).where(CMSConnector.firm_id == firm_id).order_by(CMSConnector.id)
        )
    )


# --- projects -----------------------------------------------------------------------------------


def create_project(db: Session, payload: ProjectCreate) -> OnboardingProject:
    get_firm(db, payload.firm_id)
    connector = db.scalar(
        select(CMSConnector).where(
            CMSConnector.id == payload.connector_id, CMSConnector.firm_id == payload.firm_id
        )
    )
    if connector is None:
        # Same answer whether the connector is missing or belongs to another firm.
        raise NotFound(f"Connector {payload.connector_id} not found for firm {payload.firm_id}.")
    project = OnboardingProject(
        firm_id=payload.firm_id, connector_id=connector.id, name=payload.name
    )
    db.add(project)
    db.commit()
    return project


def get_project(db: Session, project_id: int) -> OnboardingProject:
    project = db.get(OnboardingProject, project_id)
    if project is None:
        raise NotFound(f"Onboarding project {project_id} not found.")
    return project


# --- schema versions ----------------------------------------------------------------------------


def latest_schema(
    db: Session, project: OnboardingProject, kind: SchemaKind
) -> SchemaDefinition | None:
    return db.scalar(
        select(SchemaDefinition)
        .where(SchemaDefinition.project_id == project.id, SchemaDefinition.kind == kind.value)
        .order_by(SchemaDefinition.version.desc())
        .limit(1)
    )


def list_schemas(
    db: Session, project: OnboardingProject, kind: SchemaKind | None
) -> list[SchemaDefinition]:
    query = select(SchemaDefinition).where(SchemaDefinition.project_id == project.id)
    if kind is not None:
        query = query.where(SchemaDefinition.kind == kind.value)
    return list(db.scalars(query.order_by(SchemaDefinition.kind, SchemaDefinition.version)))


def get_schema(db: Session, project: OnboardingProject, schema_id: int) -> SchemaDefinition:
    schema = db.scalar(
        select(SchemaDefinition).where(
            SchemaDefinition.id == schema_id, SchemaDefinition.project_id == project.id
        )
    )
    if schema is None:
        raise NotFound(f"Schema version {schema_id} not found in project {project.id}.")
    return schema


def _store_version(
    db: Session,
    project: OnboardingProject,
    normalized: NormalizedSchema,
    **metadata: object,
) -> tuple[SchemaDefinition, bool]:
    fingerprint = normalized.fingerprint()
    latest = latest_schema(db, project, normalized.kind)
    if latest is not None and latest.fingerprint == fingerprint:
        return latest, False

    next_version = (
        db.scalar(
            select(func.max(SchemaDefinition.version)).where(
                SchemaDefinition.project_id == project.id,
                SchemaDefinition.kind == normalized.kind.value,
            )
        )
        or 0
    ) + 1
    schema = SchemaDefinition(
        firm_id=project.firm_id,
        project_id=project.id,
        kind=normalized.kind.value,
        version=next_version,
        format=normalized.format.value,
        title=normalized.title,
        fingerprint=fingerprint,
        extensions=normalized.extensions,
        supported_transformations=(
            {k: [t.value for t in v] for k, v in normalized.supported_transformations.items()}
            if normalized.supported_transformations is not None
            else None
        ),
        fields=[FieldDefinition.from_normalized(i, f) for i, f in enumerate(normalized.fields)],
        **metadata,
    )
    db.add(schema)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("A concurrent schema registration created this version first.") from None
    return schema, True


def register_source_schema(
    db: Session,
    project: OnboardingProject,
    source_format: SchemaFormat,
    content: str,
    filename: str | None,
) -> tuple[SchemaDefinition, bool]:
    normalized = normalize_source(source_format, content)
    schema, created = _store_version(db, project, normalized, original_filename=filename)
    if created:
        schema.artifact_path = _archive_source(project, schema, source_format, content)
        db.commit()
    return schema, created


def _archive_source(
    project: OnboardingProject, schema: SchemaDefinition, source_format: SchemaFormat, content: str
) -> str:
    """Keep the exact uploaded document. The path never contains client-supplied text."""
    relative = (
        Path("projects")
        / str(project.id)
        / f"source-v{schema.version}-{schema.fingerprint[:12]}.{_EXTENSIONS[source_format]}"
    )
    target = get_settings().artifacts_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return relative.as_posix()


def inspect_target(
    db: Session, project: OnboardingProject, adapter_factory: AdapterFactory
) -> tuple[SchemaDefinition, bool, int | None]:
    inspection = adapter_factory(project.connector).inspect_target()
    schema, created = _store_version(
        db, project, inspection.normalized, target_config_revision=inspection.config_revision
    )
    if created:
        db.commit()
    return schema, created, inspection.config_revision
