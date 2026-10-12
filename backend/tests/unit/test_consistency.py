"""Cross-firm normalization consistency, fingerprints, canonical model and ground-truth fixtures."""

import json
from typing import Any

import pytest

from app.domain.canonical import CANONICAL_ATTRIBUTES
from app.domain.schema_normalizer import normalize_source
from app.integrations.mock_cms import VENDOR_TO_CANONICAL
from app.schemas.normalized import DataType, NormalizedSchema, SchemaFormat
from tests.conftest import FIXTURES, SOURCE_FILES, read_fixture

FIRMS = list(SOURCE_FILES)


def _source(firm: str) -> NormalizedSchema:
    fmt, path = SOURCE_FILES[firm]
    return normalize_source(SchemaFormat(fmt), read_fixture(path))


def _target() -> NormalizedSchema:
    raw = json.loads(read_fixture(FIXTURES / "target-schemas" / "mock-cms-default.json"))
    raw.pop("_comment")
    return NormalizedSchema.model_validate(raw)


def _expected(firm: str) -> dict[str, Any]:
    return json.loads(read_fixture(FIXTURES / "expected-mappings" / f"{firm}.json"))  # type: ignore[no-any-return]


# The same real-world concept, named differently by each firm, must normalize to the same type.
EQUIVALENT_FIELDS = {
    "client name": (
        {"firm-a": "client_full_name", "firm-b": "contact_name", "firm-c": "pc_name"},
        DataType.STRING,
    ),
    "phone": (
        {"firm-a": "client_mobile", "firm-b": "contact_phone", "firm-c": "pc_tel"},
        DataType.PHONE,
    ),
    "email": (
        {"firm-a": "client_email", "firm-b": "contact_email", "firm-c": "pc_mail"},
        DataType.EMAIL,
    ),
    "matter category": (
        {"firm-a": "matter_type", "firm-b": "case_category", "firm-c": "area_of_law"},
        DataType.ENUM,
    ),
    "opposing party": (
        {"firm-a": "opposing_party", "firm-b": "adverse_party", "firm-c": "other_side"},
        DataType.STRING,
    ),
}


@pytest.mark.parametrize("concept", list(EQUIVALENT_FIELDS))
def test_equivalent_fields_normalize_to_the_same_type(concept: str) -> None:
    keys, expected_type = EQUIVALENT_FIELDS[concept]
    for firm, key in keys.items():
        field = _source(firm).field(key)
        assert field is not None, (firm, key)
        assert field.data_type is expected_type, (firm, key)


@pytest.mark.parametrize("firm", FIRMS)
def test_every_firm_normalizes_to_the_common_model(firm: str) -> None:
    schema = _source(firm)
    assert schema.kind == "source"
    assert schema.fields, firm
    for field in schema.fields:
        assert field.label, (firm, field.key)
        assert (field.options is not None) == (field.data_type is DataType.ENUM), field.key
        # Source fields never claim a canonical mapping: that is the job of mapping proposals.
        assert field.canonical_ref is None and field.entity is None


@pytest.mark.parametrize("firm", FIRMS)
def test_normalization_is_deterministic(firm: str) -> None:
    assert _source(firm).fingerprint() == _source(firm).fingerprint()


def test_fingerprint_changes_with_content() -> None:
    schema = _source("firm-a")
    changed = schema.model_copy(deep=True)
    changed.fields[0].required = not changed.fields[0].required
    assert schema.fingerprint() != changed.fingerprint()


def test_adapter_canonical_refs_exist_with_matching_types() -> None:
    target = _target()
    for vendor_key, canonical in VENDOR_TO_CANONICAL.items():
        assert canonical in CANONICAL_ATTRIBUTES, canonical
        field = target.field(vendor_key)
        assert field is not None, vendor_key
        assert field.data_type is CANONICAL_ATTRIBUTES[canonical], vendor_key
    # Vendor-specific fields are kept, flagged by the absence of a canonical reference.
    assert target.field("contact.callback_preference").canonical_ref is None  # type: ignore[union-attr]


@pytest.mark.parametrize("firm", FIRMS)
def test_expected_mappings_are_consistent_with_schemas(firm: str) -> None:
    """Guards the ground truth itself: every label must be checkable against real schemas."""
    expected, source, target = _expected(firm), _source(firm), _target()
    assert expected["source"]["format"] == SOURCE_FILES[firm][0]
    labelled = [m["source_key"] for m in expected["mappings"]]
    assert sorted(labelled) == sorted(f.key for f in source.fields), "cover each field once"

    transformations = target.supported_transformations or {}
    for m in expected["mappings"]:
        src = source.field(m["source_key"])
        assert src is not None
        if m["decision"] == "map":
            tgt = target.field(m["target_field"])
            assert tgt is not None, m
            assert tgt.data_type in transformations[m["transformation"]], m
            if "value_map" in m:
                assert tgt.data_type is DataType.ENUM
                assert set(m["value_map"]) == set(src.options or []), m
                assert set(m["value_map"].values()) <= set(tgt.options or []), m
            elif tgt.data_type is DataType.ENUM:
                pytest.fail(f"enum target without value_map: {m}")
        elif m["decision"] == "ambiguous":
            assert all(target.field(c) for c in m["candidates"]), m
        elif m["decision"] == "unsupported":
            assert src.data_type is DataType.UNSUPPORTED, m
        else:
            assert m["decision"] in {"no_target", "requires_custom_field"}, m

    targets = [m["target_field"] for m in expected["mappings"] if m["decision"] == "map"]
    assert len(targets) == len(set(targets)), "a CMS field can only be mapped once"
    required = {f.key for f in target.fields if f.required}
    assert set(expected["required_target_coverage"]) == required
    for target_key, source_key in expected["required_target_coverage"].items():
        assert any(
            m["source_key"] == source_key and m.get("target_field") == target_key
            for m in expected["mappings"]
        )
