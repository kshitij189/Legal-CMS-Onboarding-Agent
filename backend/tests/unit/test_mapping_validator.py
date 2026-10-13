"""Deterministic validator: the labelled answers pass, and each rejection class is detected."""

from typing import Any

import pytest

from app.domain.mapping_validator import validate_proposals
from app.schemas.mapping import ProposedMapping, ValidationStatus
from tests.conftest import SOURCE_FILES
from tests.mapping_helpers import ground_truth_proposals, source_schema, target_schema

INVALID, REVIEW, VALID = (
    ValidationStatus.INVALID,
    ValidationStatus.NEEDS_REVIEW,
    ValidationStatus.VALID,
)


def _one(firm: str, untrusted: set[str] | None = None, **fields: Any) -> tuple[str, list[str]]:
    base: dict[str, Any] = {"decision": "map", "confidence": 0.9, "rationale": "r"}
    proposal = ProposedMapping.model_validate({**base, **fields})
    report = validate_proposals([proposal], source_schema(firm), target_schema(), untrusted)
    result = report.mappings[0]
    return result.status, [i.code for i in result.issues]


@pytest.mark.parametrize("firm", list(SOURCE_FILES))
def test_ground_truth_has_no_invalid_mappings(firm: str) -> None:
    report = validate_proposals(
        ground_truth_proposals(firm), source_schema(firm), target_schema(), {"notes"}
    )
    invalid = [(r.source_field, r.issues) for r in report.mappings if r.status is INVALID]
    assert invalid == []
    assert report.unproposed_source_fields == []
    assert report.coverage_gaps == []


def test_ground_truth_statuses_for_firm_c() -> None:
    report = validate_proposals(
        ground_truth_proposals("firm-c"), source_schema("firm-c"), target_schema(), {"notes"}
    )
    by_field = {r.source_field: r for r in report.mappings}
    assert by_field["pc_name"].status is VALID
    assert by_field["pc_tel"].status is VALID
    assert by_field["area_of_law"].status is VALID
    review_codes = {i.code for i in by_field["review_state"].issues}
    assert {"custom_field_creation", "required_source_unmapped"} <= review_codes
    notes_codes = {i.code for i in by_field["notes"].issues}
    assert {"ambiguous_mapping", "untrusted_instruction_text"} <= notes_codes


# --- invalid: each rejection class ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("firm", "fields", "code"),
    [
        (
            "firm-a",
            {"source_field": "shoe_size", "target_field": "contact.email"},
            "unknown_source_field",
        ),
        (
            "firm-a",
            {"source_field": "client_email", "target_field": "contact.fax"},
            "unknown_target_field",
        ),
        (
            "firm-a",
            {
                "source_field": "client_full_name",
                "target_field": "matter.intake_status",
                "value_map": [],
            },
            "unauthorized_configuration_area",
        ),
        (
            "firm-a",
            {
                "source_field": "client_email",
                "target_field": "contact.email",
                "transformation": "uppercase",
            },
            "unsupported_transformation",
        ),
        (
            "firm-a",
            {
                "source_field": "client_email",
                "target_field": "contact.email",
                "transformation": "normalize_phone",
            },
            "transformation_incompatible",
        ),
        (
            "firm-a",
            {"source_field": "consent_to_contact", "target_field": "matter.jurisdiction"},
            "incompatible_types",
        ),
        (
            "firm-a",
            {"source_field": "attachments", "target_field": "matter.summary"},
            "unsupported_source_type",
        ),
        (
            "firm-a",
            {
                "source_field": "jurisdiction",
                "target_field": "matter.jurisdiction",
                "value_map": [{"source_value": "a", "target_value": "b"}],
            },
            "value_map_requires_enum_target",
        ),
        (
            "firm-a",
            {
                "source_field": "matter_type",
                "target_field": "matter.practice_area",
                "value_map": [{"source_value": "Family", "target_value": "divorce"}],
            },
            "invalid_enum_option",
        ),
        (
            "firm-a",
            {
                "source_field": "matter_type",
                "target_field": "matter.practice_area",
                "value_map": [
                    {"source_value": "Family", "target_value": "family_law"},
                    {"source_value": "Family", "target_value": "business"},
                ],
            },
            "duplicate_value_map_entry",
        ),
        # lowercase runs before the value map, so original-case keys can never match.
        (
            "firm-a",
            {
                "source_field": "matter_type",
                "target_field": "matter.practice_area",
                "transformation": "lowercase",
                "value_map": [{"source_value": "Family", "target_value": "family_law"}],
            },
            "unknown_source_option",
        ),
        (
            "firm-c",
            {
                "source_field": "review_state",
                "decision": "requires_custom_field",
                "custom_field_entity": "invoice",
                "custom_field_name": "review_status",
                "custom_field_data_type": "enum",
                "custom_field_options": ["a"],
            },
            "invalid_custom_field",
        ),
        (
            "firm-c",
            {
                "source_field": "review_state",
                "decision": "requires_custom_field",
                "custom_field_entity": "matter",
                "custom_field_name": "Review Status",
                "custom_field_data_type": "enum",
                "custom_field_options": ["a"],
            },
            "invalid_custom_field",
        ),
        (
            "firm-c",
            {
                "source_field": "review_state",
                "decision": "requires_custom_field",
                "custom_field_entity": "matter",
                "custom_field_name": "review_status",
                "custom_field_data_type": "enum",
            },
            "invalid_custom_field",
        ),
    ],
)
def test_rejection_classes(firm: str, fields: dict[str, Any], code: str) -> None:
    status, codes = _one(firm, **fields)
    assert status is INVALID, codes
    assert code in codes


def test_duplicate_source_field_is_invalid() -> None:
    p = ProposedMapping(
        source_field="client_email",
        decision="map",
        target_field="contact.email",
        transformation="lowercase",
        confidence=0.9,
        rationale="r",
    )
    report = validate_proposals([p, p], source_schema("firm-a"), target_schema())
    assert [r.status for r in report.mappings] == [VALID, INVALID]
    assert report.mappings[1].issues[0].code == "duplicate_source_field"


def test_high_confidence_never_rescues_an_invalid_mapping() -> None:
    status, _ = _one(
        "firm-a", source_field="client_email", target_field="contact.fax", confidence=1.0
    )
    assert status is INVALID


# --- needs_review classes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("firm", "fields", "code"),
    [
        (
            "firm-a",
            {
                "source_field": "client_email",
                "target_field": "contact.email",
                "transformation": "lowercase",
                "confidence": 0.4,
            },
            "low_confidence",
        ),
        (
            "firm-a",
            {"source_field": "matter_details", "target_field": "matter.jurisdiction"},
            "possible_truncation",
        ),
        (
            "firm-a",
            {"source_field": "client_mobile", "target_field": "contact.phone_number"},
            "phone_not_normalized",
        ),
        (
            "firm-a",
            {"source_field": "jurisdiction", "target_field": "matter.practice_area"},
            "free_text_to_enum",
        ),
        (
            "firm-a",
            {
                "source_field": "matter_type",
                "target_field": "matter.practice_area",
                "value_map": [{"source_value": "Family", "target_value": "family_law"}],
            },
            "unmapped_source_options",
        ),
        (
            "firm-a",
            {
                "source_field": "client_full_name",
                "decision": "ambiguous",
                "candidates": ["contact.full_name", "contact.nickname"],
            },
            "unknown_candidate",
        ),
        (
            "firm-a",
            {"source_field": "client_mobile", "decision": "no_target"},
            "required_source_unmapped",
        ),
        (
            "firm-a",
            {"source_field": "client_email", "decision": "unsupported"},
            "unsupported_declared",
        ),
    ],
)
def test_needs_review_classes(firm: str, fields: dict[str, Any], code: str) -> None:
    status, codes = _one(firm, **fields)
    assert status is REVIEW, codes
    assert code in codes


def test_untrusted_text_forces_review_even_for_a_clean_mapping() -> None:
    fields: dict[str, Any] = {"source_field": "notes", "target_field": "matter.summary"}
    assert _one("firm-c", **fields)[0] is VALID
    status, codes = _one("firm-c", untrusted={"notes"}, **fields)
    assert status is REVIEW and "untrusted_instruction_text" in codes


def test_duplicate_targets_coverage_gaps_and_unproposed_fields() -> None:
    def to_email(source: str) -> ProposedMapping:
        return ProposedMapping(
            source_field=source,
            decision="map",
            target_field="contact.email",
            transformation="lowercase",
            confidence=0.9,
            rationale="r",
        )

    report = validate_proposals(
        [to_email("client_email"), to_email("client_full_name")],
        source_schema("firm-a"),
        target_schema(),
    )
    assert all("duplicate_target" in [i.code for i in r.issues] for r in report.mappings)
    assert {g.target_field for g in report.coverage_gaps} == {
        "contact.full_name",
        "matter.practice_area",
    }
    assert "client_mobile" in report.unproposed_source_fields
