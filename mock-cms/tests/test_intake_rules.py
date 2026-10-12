"""Unit tests for transformation and validation rules (no database or HTTP)."""

import pytest

from app.catalog import DataType, Transformation
from app.models import FieldDefinition, IntakeMapping
from app.security import hash_password, hash_token, verify_password
from app.services.intake_service import (
    InvalidValue,
    apply_transformation,
    coerce_value,
    process_submission,
)


@pytest.mark.parametrize(
    ("transformation", "raw", "expected"),
    [
        (Transformation.NONE, " Jane ", " Jane "),
        (Transformation.TRIM, "  Jane Doe ", "Jane Doe"),
        (Transformation.LOWERCASE, " Jane@Example.COM ", "jane@example.com"),
        (Transformation.NORMALIZE_PHONE, "(555) 123-4567", "5551234567"),
        (Transformation.NORMALIZE_PHONE, "+44 20.7946.0958", "+442079460958"),
        (Transformation.NORMALIZE_PHONE, "0044 20 7946 0958", "+442079460958"),
    ],
)
def test_apply_transformation(transformation: Transformation, raw: str, expected: str) -> None:
    assert apply_transformation(transformation, raw) == expected


@pytest.mark.parametrize(
    ("data_type", "value", "options", "expected"),
    [
        (DataType.STRING, "abc", None, "abc"),
        (DataType.EMAIL, "a@b.co", None, "a@b.co"),
        (DataType.PHONE, "+15551234567", None, "+15551234567"),
        (DataType.ENUM, "employment", ["employment"], "employment"),
        (DataType.DATE, "2026-01-31", None, "2026-01-31"),
        (DataType.BOOLEAN, "TRUE", None, True),
        (DataType.BOOLEAN, False, None, False),
        (DataType.NUMBER, "12.5", None, 12.5),
        (DataType.NUMBER, 3, None, 3),
    ],
)
def test_coerce_value_accepts(
    data_type: DataType, value: object, options: list[str] | None, expected: object
) -> None:
    assert coerce_value(data_type, value, options) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("data_type", "value", "options", "code"),
    [
        (DataType.STRING, 42, None, "invalid_type"),
        (DataType.STRING, "x" * 256, None, "too_long"),
        (DataType.EMAIL, "not-an-email", None, "invalid_email"),
        (DataType.PHONE, "(555) 123-4567", None, "invalid_phone"),
        (DataType.PHONE, "123", None, "invalid_phone"),
        (DataType.ENUM, "tax", ["employment"], "invalid_option"),
        (DataType.DATE, "31/01/2026", None, "invalid_date"),
        (DataType.BOOLEAN, "maybe", None, "invalid_boolean"),
        (DataType.NUMBER, "twelve", None, "invalid_number"),
        (DataType.NUMBER, True, None, "invalid_number"),
    ],
)
def test_coerce_value_rejects(
    data_type: DataType, value: object, options: list[str] | None, code: str
) -> None:
    with pytest.raises(InvalidValue) as exc:
        coerce_value(data_type, value, options)  # type: ignore[arg-type]
    assert exc.value.code == code


def _field(
    id_: int, key: str, data_type: DataType, required: bool = False, **kw: object
) -> FieldDefinition:
    return FieldDefinition(
        id=id_,
        key=key,
        entity=key.split(".")[0],
        label=key,
        data_type=data_type.value,
        required=required,
        enabled=kw.get("enabled", True),
        options=kw.get("options"),
    )


def _mapping(
    source: str,
    field_id: int,
    transformation: Transformation = Transformation.NONE,
    value_map: dict[str, str] | None = None,
) -> IntakeMapping:
    return IntakeMapping(
        source_key=source,
        target_field_id=field_id,
        transformation=transformation.value,
        value_map=value_map,
    )


FIELDS = [
    _field(1, "contact.full_name", DataType.STRING, required=True),
    _field(2, "contact.phone_number", DataType.PHONE),
    _field(
        3,
        "matter.practice_area",
        DataType.ENUM,
        required=True,
        options=["family_law", "employment"],
    ),
    _field(4, "contact.email", DataType.EMAIL, required=True),
]


def test_process_submission_applies_transformations_and_value_map() -> None:
    mappings = [
        _mapping("client_name", 1, Transformation.TRIM),
        _mapping("client_mobile", 2, Transformation.NORMALIZE_PHONE),
        _mapping("case_type", 3, Transformation.LOWERCASE, {"workplace": "employment"}),
        _mapping("client_email", 4),
    ]
    result = process_submission(
        FIELDS,
        mappings,
        {
            "client_name": " Jane Doe ",
            "client_mobile": "(555) 123-4567",
            "case_type": "Workplace",
            "client_email": "jane@example.com",
            "extra": "x",
        },
    )
    assert result.errors == []
    assert result.values == {
        "contact.full_name": "Jane Doe",
        "contact.phone_number": "5551234567",
        "matter.practice_area": "employment",
        "contact.email": "jane@example.com",
    }
    assert result.unmapped_keys == ["extra"]


def test_process_submission_reports_missing_required_and_invalid_values() -> None:
    mappings = [_mapping("client_name", 1), _mapping("client_mobile", 2)]
    result = process_submission(
        FIELDS, mappings, {"client_name": "  ", "client_mobile": "(555) 123-4567"}
    )
    by_field = {e["field"]: e for e in result.errors}
    assert by_field["contact.phone_number"]["code"] == "invalid_phone"
    assert by_field["contact.full_name"]["code"] == "required_field_missing"
    assert by_field["contact.full_name"]["source_key"] == "client_name"
    # Required but no mapping at all: a configuration gap, reported without a source key.
    assert by_field["matter.practice_area"]["source_key"] is None
    assert "No intake mapping" in by_field["matter.practice_area"]["message"]
    assert result.values == {}


def test_disabled_required_field_is_not_enforced() -> None:
    fields = [_field(1, "contact.full_name", DataType.STRING, required=True, enabled=False)]
    assert process_submission(fields, [], {}).errors == []


def test_password_hash_roundtrip() -> None:
    stored = hash_password("s3cret")
    assert stored.startswith("scrypt$")
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret", "garbage")


def test_token_hash_is_deterministic_and_not_plaintext() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != "abc"
