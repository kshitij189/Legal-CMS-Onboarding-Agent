from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import require
from app.api.serializers import schema_detail, schema_summary
from app.db.session import get_db
from app.domain import project_service
from app.integrations.registry import AdapterFactory, build_adapter
from app.models import User
from app.permissions import Permission
from app.schemas.api import (
    SchemaRegistrationOut,
    SchemaVersionOut,
    SchemaVersionSummary,
    SourceSchemaUpload,
)
from app.schemas.normalized import SchemaFormat, SchemaKind

router = APIRouter(prefix="/api/v1/onboarding-projects/{project_id}", tags=["schemas"])


def get_adapter_factory() -> AdapterFactory:
    """Overridable in tests; production builds adapters from the connector record."""
    return build_adapter


@router.post("/source-schema", response_model=SchemaRegistrationOut)
def register_source_schema(
    project_id: int,
    body: SourceSchemaUpload,
    response: Response,
    user: User = Depends(require(Permission.SOURCE_SCHEMA_WRITE)),
    db: Session = Depends(get_db),
) -> SchemaRegistrationOut:
    project = project_service.get_project(db, user, project_id)
    schema, created = project_service.register_source_schema(
        db, user, project, SchemaFormat(body.format), body.content, body.filename
    )
    response.status_code = 201 if created else 200
    return SchemaRegistrationOut(created=created, schema_version=schema_detail(schema))


@router.post("/inspect-target", response_model=SchemaRegistrationOut)
def inspect_target(
    project_id: int,
    response: Response,
    user: User = Depends(require(Permission.TARGET_INSPECT)),
    db: Session = Depends(get_db),
    adapter_factory: AdapterFactory = Depends(get_adapter_factory),
) -> SchemaRegistrationOut:
    project = project_service.get_project(db, user, project_id)
    schema, created, revision = project_service.inspect_target(db, user, project, adapter_factory)
    response.status_code = 201 if created else 200
    return SchemaRegistrationOut(
        created=created, schema_version=schema_detail(schema), observed_config_revision=revision
    )


@router.get("/schemas", response_model=list[SchemaVersionSummary])
def list_schemas(
    project_id: int,
    kind: SchemaKind | None = None,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> list[SchemaVersionSummary]:
    project = project_service.get_project(db, user, project_id)
    return [schema_summary(s) for s in project_service.list_schemas(db, project, kind)]


@router.get("/schemas/{schema_id}", response_model=SchemaVersionOut)
def get_schema(
    project_id: int,
    schema_id: int,
    user: User = Depends(require(Permission.PROJECTS_READ)),
    db: Session = Depends(get_db),
) -> SchemaVersionOut:
    project = project_service.get_project(db, user, project_id)
    return schema_detail(project_service.get_schema(db, project, schema_id))
