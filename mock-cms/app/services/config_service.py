"""Tenant-scoped configuration: field settings, custom fields and intake mappings.

Every function takes the tenant id explicitly; callers derive it from the authenticated user, never
from request input. Each effective change bumps the tenant's config_revision in the same
transaction; no-op writes do not, so repeated identical requests are idempotent.
"""

import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog import BUILTIN_FIELDS, TRANSFORMATION_TARGET_TYPES, DataType
from app.errors import Conflict, NotFound, ValidationFailed
from app.models import FieldDefinition, IntakeMapping, Tenant
from app.schemas import SOURCE_KEY_PATTERN, CustomFieldCreate, FieldUpdate, MappingUpsert

_SOURCE_KEY_RE = re.compile(SOURCE_KEY_PATTERN)


def create_tenant(db: Session, slug: str, name: str) -> Tenant:
    tenant = Tenant(slug=slug, name=name, config_revision=0)
    db.add(tenant)
    db.flush()
    for f in BUILTIN_FIELDS:
        db.add(
            FieldDefinition(
                tenant_id=tenant.id,
                key=f.key,
                entity=f.entity.value,
                label=f.label,
                description=f.description,
                data_type=f.data_type.value,
                required=f.required,
                enabled=True,
                is_custom=False,
                options=list(f.options) if f.options else None,
            )
        )
    db.flush()
    return tenant


def current_revision(db: Session, tenant_id: int) -> int:
    revision = db.scalar(select(Tenant.config_revision).where(Tenant.id == tenant_id))
    if revision is None:
        raise NotFound("Tenant not found.")
    return revision


def _bump_revision(db: Session, tenant_id: int) -> int:
    return db.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(config_revision=Tenant.config_revision + 1)
        .returning(Tenant.config_revision)
    ).scalar_one()


def list_fields(db: Session, tenant_id: int) -> list[FieldDefinition]:
    return list(
        db.scalars(
            select(FieldDefinition)
            .where(FieldDefinition.tenant_id == tenant_id)
            .order_by(FieldDefinition.entity, FieldDefinition.is_custom, FieldDefinition.id)
        )
    )


def get_field(db: Session, tenant_id: int, key: str) -> FieldDefinition:
    field = db.scalar(
        select(FieldDefinition).where(
            FieldDefinition.tenant_id == tenant_id, FieldDefinition.key == key
        )
    )
    if field is None:
        raise NotFound(f"Field '{key}' does not exist.", [{"field": key, "code": "unknown_field"}])
    return field


def _mappings_targeting(db: Session, field: FieldDefinition) -> list[IntakeMapping]:
    return list(
        db.scalars(
            select(IntakeMapping).where(
                IntakeMapping.tenant_id == field.tenant_id,
                IntakeMapping.target_field_id == field.id,
            )
        )
    )


def _check_options(data_type: str, options: list[str] | None, key: str) -> None:
    if data_type == DataType.ENUM:
        if not options:
            raise ValidationFailed(
                "Enum fields need at least one option.",
                [{"field": key, "code": "options_required"}],
            )
        if len(set(options)) != len(options):
            raise ValidationFailed(
                "Enum options must be unique.", [{"field": key, "code": "duplicate_option"}]
            )
    elif options is not None:
        raise ValidationFailed(
            "Only enum fields accept options.", [{"field": key, "code": "options_not_allowed"}]
        )


def update_field(
    db: Session, tenant_id: int, key: str, changes: FieldUpdate
) -> tuple[FieldDefinition, bool, int]:
    field = get_field(db, tenant_id, key)
    requested: dict[str, Any] = changes.model_dump(exclude_unset=True, exclude_none=True)

    if "options" in requested:
        _check_options(field.data_type, requested["options"], key)
        for mapping in _mappings_targeting(db, field):
            orphaned = set((mapping.value_map or {}).values()) - set(requested["options"])
            if orphaned:
                raise Conflict(
                    "Mapping value_map references options that would be removed.",
                    [
                        {
                            "field": key,
                            "source_key": mapping.source_key,
                            "code": "option_in_use",
                            "options": sorted(orphaned),
                        }
                    ],
                )
    if requested.get("enabled") is False and _mappings_targeting(db, field):
        raise Conflict(
            "Cannot disable a field that an intake mapping targets.",
            [{"field": key, "code": "field_in_use"}],
        )

    effective = {k: v for k, v in requested.items() if getattr(field, k) != v}
    if not effective:
        return field, False, current_revision(db, tenant_id)
    for name, value in effective.items():
        setattr(field, name, value)
    revision = _bump_revision(db, tenant_id)
    db.commit()
    return field, True, revision


def create_custom_field(
    db: Session, tenant_id: int, payload: CustomFieldCreate
) -> tuple[FieldDefinition, int]:
    key = f"{payload.entity.value}.custom.{payload.name}"
    _check_options(payload.data_type, payload.options, key)
    field = FieldDefinition(
        tenant_id=tenant_id,
        key=key,
        entity=payload.entity.value,
        label=payload.label,
        description=payload.description,
        data_type=payload.data_type.value,
        required=payload.required,
        enabled=True,
        is_custom=True,
        options=payload.options,
    )
    db.add(field)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(
            f"Field '{key}' already exists.", [{"field": key, "code": "duplicate_field"}]
        ) from None
    revision = _bump_revision(db, tenant_id)
    db.commit()
    return field, revision


def list_mappings(db: Session, tenant_id: int) -> list[IntakeMapping]:
    return list(
        db.scalars(
            select(IntakeMapping)
            .where(IntakeMapping.tenant_id == tenant_id)
            .order_by(IntakeMapping.source_key)
        )
    )


def _get_mapping(db: Session, tenant_id: int, source_key: str) -> IntakeMapping | None:
    return db.scalar(
        select(IntakeMapping).where(
            IntakeMapping.tenant_id == tenant_id, IntakeMapping.source_key == source_key
        )
    )


def _validate_mapping(target: FieldDefinition, payload: MappingUpsert) -> None:
    details: list[dict[str, Any]] = []
    if not target.enabled:
        details.append({"field": target.key, "code": "target_disabled"})
    allowed = TRANSFORMATION_TARGET_TYPES[payload.transformation]
    if DataType(target.data_type) not in allowed:
        details.append(
            {
                "field": target.key,
                "code": "unsupported_transformation",
                "transformation": payload.transformation.value,
                "target_type": target.data_type,
            }
        )
    if payload.value_map is not None:
        if target.data_type != DataType.ENUM:
            details.append({"field": target.key, "code": "value_map_requires_enum"})
        else:
            invalid = sorted(set(payload.value_map.values()) - set(target.options or []))
            if invalid:
                details.append(
                    {"field": target.key, "code": "invalid_enum_option", "options": invalid}
                )
    if details:
        raise ValidationFailed("Mapping is not valid for the target field.", details)


def upsert_mapping(
    db: Session, tenant_id: int, source_key: str, payload: MappingUpsert
) -> tuple[IntakeMapping, bool, int]:
    if not _SOURCE_KEY_RE.match(source_key):
        raise ValidationFailed(
            "Invalid source key.",
            [
                {
                    "source_key": source_key,
                    "code": "invalid_source_key",
                    "message": "Use 1-100 characters: letters, digits, '_', '.', '-'.",
                }
            ],
        )
    try:
        target = get_field(db, tenant_id, payload.target_field)
    except NotFound as exc:
        raise ValidationFailed(exc.message, exc.details) from None
    _validate_mapping(target, payload)

    holder = db.scalar(
        select(IntakeMapping).where(
            IntakeMapping.tenant_id == tenant_id, IntakeMapping.target_field_id == target.id
        )
    )
    if holder is not None and holder.source_key != source_key:
        raise Conflict(
            f"Field '{target.key}' is already mapped from '{holder.source_key}'.",
            [
                {
                    "field": target.key,
                    "code": "target_already_mapped",
                    "source_key": holder.source_key,
                }
            ],
        )

    mapping = _get_mapping(db, tenant_id, source_key)
    desired = (target.id, payload.transformation.value, payload.value_map)
    if (
        mapping is not None
        and (mapping.target_field_id, mapping.transformation, mapping.value_map) == desired
    ):
        return mapping, False, current_revision(db, tenant_id)

    if mapping is None:
        mapping = IntakeMapping(tenant_id=tenant_id, source_key=source_key)
        db.add(mapping)
    mapping.target_field_id = target.id
    mapping.target_field = target
    mapping.transformation = payload.transformation.value
    mapping.value_map = payload.value_map
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(
            "Mapping conflicts with a concurrent change.", [{"code": "concurrent_change"}]
        ) from None
    revision = _bump_revision(db, tenant_id)
    db.commit()
    return mapping, True, revision


def delete_mapping(db: Session, tenant_id: int, source_key: str) -> int:
    mapping = _get_mapping(db, tenant_id, source_key)
    if mapping is None:
        raise NotFound(
            f"No mapping for source key '{source_key}'.",
            [{"source_key": source_key, "code": "unknown_mapping"}],
        )
    db.delete(mapping)
    revision = _bump_revision(db, tenant_id)
    db.commit()
    return revision
