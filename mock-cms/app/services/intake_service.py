"""Intake form submission: apply the tenant's mappings, validate against field rules, persist.

The pure functions (apply_transformation, coerce_value, process_submission) carry the rules and are
unit-tested without a database.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import DataType, Transformation
from app.errors import NotFound, ValidationFailed
from app.models import FieldDefinition, IntakeMapping, IntakeRecord, User
from app.schemas import SubmittedValue
from app.services.config_service import current_revision, list_fields, list_mappings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
_PHONE_FORMATTING_RE = re.compile(r"[\s().\-]")
_MAX_LENGTH = {DataType.STRING: 255, DataType.TEXT: 10_000}


class InvalidValue(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def apply_transformation(transformation: Transformation, value: str) -> str:
    match transformation:
        case Transformation.NONE:
            return value
        case Transformation.TRIM:
            return value.strip()
        case Transformation.LOWERCASE:
            return value.strip().lower()
        case Transformation.NORMALIZE_PHONE:
            normalized = _PHONE_FORMATTING_RE.sub("", value)
            if normalized.startswith("00"):
                normalized = "+" + normalized[2:]
            return normalized


def coerce_value(data_type: DataType, value: SubmittedValue, options: list[str] | None) -> Any:
    """Return the stored representation of `value`, or raise InvalidValue."""
    match data_type:
        case DataType.STRING | DataType.TEXT:
            if not isinstance(value, str):
                raise InvalidValue("invalid_type", "Expected text.")
            if len(value) > _MAX_LENGTH[data_type]:
                raise InvalidValue("too_long", f"At most {_MAX_LENGTH[data_type]} characters.")
            return value
        case DataType.EMAIL:
            if not isinstance(value, str) or not _EMAIL_RE.match(value):
                raise InvalidValue("invalid_email", "Expected an email address.")
            return value
        case DataType.PHONE:
            if not isinstance(value, str) or not _PHONE_RE.match(value):
                raise InvalidValue(
                    "invalid_phone", "Expected 7-15 digits with an optional leading '+'."
                )
            return value
        case DataType.ENUM:
            if not isinstance(value, str) or value not in (options or []):
                raise InvalidValue(
                    "invalid_option", f"Expected one of: {', '.join(options or [])}."
                )
            return value
        case DataType.DATE:
            try:
                return date.fromisoformat(str(value)).isoformat()
            except ValueError:
                raise InvalidValue("invalid_date", "Expected a date as YYYY-MM-DD.") from None
        case DataType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise InvalidValue("invalid_boolean", "Expected true or false.")
        case DataType.NUMBER:
            if isinstance(value, bool):
                raise InvalidValue("invalid_number", "Expected a number.")
            try:
                number = float(value) if isinstance(value, str) else value
            except ValueError:
                raise InvalidValue("invalid_number", "Expected a number.") from None
            if not isinstance(number, int | float):
                raise InvalidValue("invalid_number", "Expected a number.")
            return number


def _is_blank(value: SubmittedValue) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


@dataclass
class SubmissionResult:
    values: dict[str, Any] = field(default_factory=dict)
    unmapped_keys: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def process_submission(
    fields: list[FieldDefinition],
    mappings: list[IntakeMapping],
    submitted: dict[str, SubmittedValue],
) -> SubmissionResult:
    result = SubmissionResult()
    by_id = {f.id: f for f in fields}
    errored: set[str] = set()

    for mapping in mappings:
        target = by_id[mapping.target_field_id]
        raw = submitted.get(mapping.source_key)
        if _is_blank(raw):
            continue
        value: SubmittedValue = raw
        if isinstance(value, str):
            value = apply_transformation(Transformation(mapping.transformation), value)
            if mapping.value_map and value in mapping.value_map:
                value = mapping.value_map[value]
        try:
            result.values[target.key] = coerce_value(
                DataType(target.data_type), value, target.options
            )
        except InvalidValue as exc:
            errored.add(target.key)
            result.errors.append(
                {
                    "field": target.key,
                    "source_key": mapping.source_key,
                    "code": exc.code,
                    "message": exc.message,
                }
            )

    mapped_targets = {by_id[m.target_field_id].key: m.source_key for m in mappings}
    for f in fields:
        if f.enabled and f.required and f.key not in result.values and f.key not in errored:
            source_key = mapped_targets.get(f.key)
            result.errors.append(
                {
                    "field": f.key,
                    "source_key": source_key,
                    "code": "required_field_missing",
                    "message": f"'{f.label}' is required."
                    + ("" if source_key else " No intake mapping provides this field."),
                }
            )

    mapped_sources = {m.source_key for m in mappings}
    result.unmapped_keys = sorted(k for k in submitted if k not in mapped_sources)
    return result


def submit_intake(db: Session, user: User, submitted: dict[str, SubmittedValue]) -> IntakeRecord:
    result = process_submission(
        list_fields(db, user.tenant_id), list_mappings(db, user.tenant_id), submitted
    )
    if result.errors:
        raise ValidationFailed("Intake submission failed validation.", result.errors)
    record = IntakeRecord(
        tenant_id=user.tenant_id,
        values=result.values,
        unmapped_keys=result.unmapped_keys,
        config_revision=current_revision(db, user.tenant_id),
        submitted_by_id=user.id,
    )
    db.add(record)
    db.commit()
    return record


def list_records(db: Session, tenant_id: int, limit: int = 100) -> list[IntakeRecord]:
    return list(
        db.scalars(
            select(IntakeRecord)
            .where(IntakeRecord.tenant_id == tenant_id)
            .order_by(IntakeRecord.id.desc())
            .limit(limit)
        )
    )


def get_record(db: Session, tenant_id: int, record_id: int) -> IntakeRecord:
    record = db.scalar(
        select(IntakeRecord).where(
            IntakeRecord.tenant_id == tenant_id, IntakeRecord.id == record_id
        )
    )
    if record is None:
        raise NotFound(f"Intake record {record_id} not found.")
    return record
