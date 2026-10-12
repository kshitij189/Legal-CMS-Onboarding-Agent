"""JSON API (/api/v1). The tenant always comes from the authenticated user, never the request."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.catalog import TRANSFORMATION_TARGET_TYPES, DataType, Entity, Transformation
from app.db import get_db
from app.deps import api_session, require
from app.errors import Unauthenticated
from app.models import AuthSession, FieldDefinition, IntakeMapping, IntakeRecord, Tenant, User
from app.permissions import ROLE_PERMISSIONS, Permission, Role
from app.schemas import (
    ConfigSnapshot,
    CustomFieldCreate,
    FieldOut,
    FieldUpdate,
    FieldWriteResponse,
    IntakeRecordOut,
    IntakeSubmission,
    MappingDeleteResponse,
    MappingOut,
    MappingUpsert,
    MappingWriteResponse,
    MeResponse,
    SchemaResponse,
    TenantOut,
    TokenRequest,
    TokenResponse,
    TransformationOut,
)
from app.services import auth_service, config_service, intake_service

router = APIRouter(prefix="/api/v1")


def _tenant(tenant: Tenant) -> TenantOut:
    return TenantOut(slug=tenant.slug, name=tenant.name)


def _field(field: FieldDefinition) -> FieldOut:
    return FieldOut.model_validate(field)


def _mapping(mapping: IntakeMapping) -> MappingOut:
    return MappingOut(
        source_key=mapping.source_key,
        target_field=mapping.target_field.key,
        transformation=Transformation(mapping.transformation),
        value_map=mapping.value_map,
        updated_at=mapping.updated_at,
    )


def _record(record: IntakeRecord) -> IntakeRecordOut:
    return IntakeRecordOut(
        id=record.id,
        values=record.values,
        unmapped_keys=record.unmapped_keys,
        config_revision=record.config_revision,
        submitted_by=record.submitted_by.username,
        created_at=record.created_at,
    )


@router.post("/auth/token", response_model=TokenResponse)
def issue_token(body: TokenRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = auth_service.authenticate(db, body.username, body.password)
    if user is None:
        raise Unauthenticated("Invalid username or password.")
    token, session = auth_service.create_session(db, user, kind="api")
    return TokenResponse(access_token=token, expires_at=session.expires_at)


@router.post("/auth/logout", status_code=204)
def logout(session: AuthSession = Depends(api_session), db: Session = Depends(get_db)) -> Response:
    auth_service.revoke_session(db, session.id)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
def me(session: AuthSession = Depends(api_session)) -> MeResponse:
    user = session.user
    return MeResponse(
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        tenant=_tenant(user.tenant),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )


@router.get("/schema", response_model=SchemaResponse)
def schema(
    user: User = Depends(require(Permission.SCHEMA_READ)), db: Session = Depends(get_db)
) -> SchemaResponse:
    return SchemaResponse(
        tenant=_tenant(user.tenant),
        config_revision=config_service.current_revision(db, user.tenant_id),
        entities=list(Entity),
        data_types=list(DataType),
        transformations=[
            TransformationOut(name=t, applicable_types=sorted(types))
            for t, types in TRANSFORMATION_TARGET_TYPES.items()
        ],
        fields=[_field(f) for f in config_service.list_fields(db, user.tenant_id) if f.enabled],
    )


@router.get("/config", response_model=ConfigSnapshot)
def config_snapshot(
    user: User = Depends(require(Permission.CONFIG_READ)), db: Session = Depends(get_db)
) -> ConfigSnapshot:
    return ConfigSnapshot(
        tenant=_tenant(user.tenant),
        config_revision=config_service.current_revision(db, user.tenant_id),
        fields=[_field(f) for f in config_service.list_fields(db, user.tenant_id)],
        intake_mappings=[_mapping(m) for m in config_service.list_mappings(db, user.tenant_id)],
    )


@router.patch("/config/fields/{field_key}", response_model=FieldWriteResponse)
def update_field(
    field_key: str,
    body: FieldUpdate,
    user: User = Depends(require(Permission.FIELDS_WRITE)),
    db: Session = Depends(get_db),
) -> FieldWriteResponse:
    field, changed, revision = config_service.update_field(db, user.tenant_id, field_key, body)
    return FieldWriteResponse(field=_field(field), changed=changed, config_revision=revision)


@router.post("/config/custom-fields", response_model=FieldWriteResponse, status_code=201)
def create_custom_field(
    body: CustomFieldCreate,
    user: User = Depends(require(Permission.FIELDS_WRITE)),
    db: Session = Depends(get_db),
) -> FieldWriteResponse:
    field, revision = config_service.create_custom_field(db, user.tenant_id, body)
    return FieldWriteResponse(field=_field(field), changed=True, config_revision=revision)


@router.get("/config/intake-mappings", response_model=list[MappingOut])
def list_mappings(
    user: User = Depends(require(Permission.CONFIG_READ)), db: Session = Depends(get_db)
) -> list[MappingOut]:
    return [_mapping(m) for m in config_service.list_mappings(db, user.tenant_id)]


@router.put("/config/intake-mappings/{source_key}", response_model=MappingWriteResponse)
def upsert_mapping(
    source_key: str,
    body: MappingUpsert,
    user: User = Depends(require(Permission.MAPPINGS_WRITE)),
    db: Session = Depends(get_db),
) -> MappingWriteResponse:
    mapping, changed, revision = config_service.upsert_mapping(db, user.tenant_id, source_key, body)
    return MappingWriteResponse(
        mapping=_mapping(mapping), changed=changed, config_revision=revision
    )


@router.delete("/config/intake-mappings/{source_key}", response_model=MappingDeleteResponse)
def delete_mapping(
    source_key: str,
    user: User = Depends(require(Permission.MAPPINGS_WRITE)),
    db: Session = Depends(get_db),
) -> MappingDeleteResponse:
    revision = config_service.delete_mapping(db, user.tenant_id, source_key)
    return MappingDeleteResponse(source_key=source_key, config_revision=revision)


@router.post("/intake-submissions", response_model=IntakeRecordOut, status_code=201)
def submit_intake(
    body: IntakeSubmission,
    user: User = Depends(require(Permission.INTAKE_SUBMIT)),
    db: Session = Depends(get_db),
) -> IntakeRecordOut:
    return _record(intake_service.submit_intake(db, user, body.values))


@router.get("/intake-records", response_model=list[IntakeRecordOut])
def list_records(
    user: User = Depends(require(Permission.RECORDS_READ)), db: Session = Depends(get_db)
) -> list[IntakeRecordOut]:
    return [_record(r) for r in intake_service.list_records(db, user.tenant_id)]


@router.get("/intake-records/{record_id}", response_model=IntakeRecordOut)
def get_record(
    record_id: int,
    user: User = Depends(require(Permission.RECORDS_READ)),
    db: Session = Depends(get_db),
) -> IntakeRecordOut:
    return _record(intake_service.get_record(db, user.tenant_id, record_id))
