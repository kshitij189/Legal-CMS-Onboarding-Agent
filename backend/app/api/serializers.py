from sqlalchemy.orm import Session

from app.domain import project_service
from app.models import OnboardingProject, SchemaDefinition
from app.schemas.api import (
    ConnectorOut,
    FirmOut,
    ProjectOut,
    SchemaVersionOut,
    SchemaVersionSummary,
)
from app.schemas.normalized import SchemaFormat, SchemaKind


def schema_summary(schema: SchemaDefinition) -> SchemaVersionSummary:
    return SchemaVersionSummary(
        id=schema.id,
        kind=SchemaKind(schema.kind),
        version=schema.version,
        format=SchemaFormat(schema.format),
        title=schema.title,
        fingerprint=schema.fingerprint,
        field_count=len(schema.fields),
        target_config_revision=schema.target_config_revision,
        original_filename=schema.original_filename,
        created_by=schema.created_by.username if schema.created_by else None,
        created_at=schema.created_at,
    )


def schema_detail(schema: SchemaDefinition) -> SchemaVersionOut:
    normalized = schema.to_normalized()
    return SchemaVersionOut(
        **schema_summary(schema).model_dump(),
        fields=normalized.fields,
        supported_transformations=schema.supported_transformations,
        extensions=schema.extensions,
    )


def project_out(db: Session, project: OnboardingProject) -> ProjectOut:
    source = project_service.latest_schema(db, project, SchemaKind.SOURCE)
    target = project_service.latest_schema(db, project, SchemaKind.TARGET)
    if source is None:
        stage = "awaiting_source_schema"
    elif target is None:
        stage = "awaiting_target_inspection"
    else:
        stage = "ready_for_mapping"
    return ProjectOut(
        id=project.id,
        firm=FirmOut.model_validate(project.firm),
        connector=ConnectorOut.model_validate(project.connector),
        name=project.name,
        created_by=project.created_by.username if project.created_by else None,
        stage=stage,
        latest_source_schema=schema_summary(source) if source else None,
        latest_target_schema=schema_summary(target) if target else None,
        created_at=project.created_at,
    )
