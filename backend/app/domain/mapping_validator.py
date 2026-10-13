"""Deterministic validation of mapping proposals (blueprint §6: "the model proposes the mapping;
deterministic application code validates it").

Pure functions over normalized schemas, no I/O. Each proposal gets a status:
- invalid       — must not be approved as-is (nonexistent field, unsupported or incompatible
                  transformation/type, unauthorized configuration area, bad value map, ...)
- needs_review  — plausible, but a human must look (ambiguity, custom field creation, low
                  confidence, lossy conversion, untrusted instruction-like text, ...)
- valid         — passes every rule. Still subject to human approval.
Model confidence can only lower a status, never raise it.
"""

import re
from collections import Counter
from collections.abc import Callable

from app.schemas.mapping import (
    CoverageGap,
    Decision,
    MappingValidation,
    ProposedMapping,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)
from app.schemas.normalized import DataType, NormalizedField, NormalizedSchema

LOW_CONFIDENCE = 0.6
# Canonical attributes owned by the CMS workflow; intake mappings may not write them.
SYSTEM_MANAGED_CANONICAL = frozenset({"matter.intake_status"})
CUSTOM_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
CREATABLE_TYPES = frozenset(DataType) - {DataType.UNSUPPORTED}


# How transformations change a value before the value map is applied (mirrors CMS semantics).
def _identity(value: str) -> str:
    return value


_PREVIEW: dict[str, Callable[[str], str]] = {
    "none": _identity,
    "trim": str.strip,
    "lowercase": lambda v: v.strip().lower(),
}

_INVALID, _REVIEW = ValidationStatus.INVALID, ValidationStatus.NEEDS_REVIEW


class _Issues:
    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []

    def add(self, severity: ValidationStatus, code: str, message: str) -> None:
        self.items.append(ValidationIssue(code=code, message=message, severity=severity))

    def status(self) -> ValidationStatus:
        severities = {i.severity for i in self.items}
        if _INVALID in severities:
            return _INVALID
        return _REVIEW if _REVIEW in severities else ValidationStatus.VALID


def _check_types(src: NormalizedField, tgt: NormalizedField, issues: _Issues, xform: str) -> None:
    s, t = src.data_type, tgt.data_type
    if s is DataType.UNSUPPORTED:
        issues.add(_INVALID, "unsupported_source_type", f"'{src.key}' has an unsupported type.")
        return
    if t is DataType.UNSUPPORTED:
        issues.add(_INVALID, "unsupported_target_type", f"'{tgt.key}' has an unsupported type.")
        return
    if s is t and t is not DataType.PHONE:
        return
    free_text = {DataType.STRING, DataType.TEXT}
    textual = free_text | {DataType.EMAIL, DataType.PHONE, DataType.ENUM, DataType.DATE}
    match t:
        case DataType.STRING:
            if s is DataType.TEXT:
                issues.add(_REVIEW, "possible_truncation", "Long text into a short text field.")
            elif s not in textual:
                issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")
        case DataType.TEXT:
            if s not in textual:
                issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")
        case DataType.PHONE:
            if s not in {DataType.PHONE, DataType.STRING}:
                issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")
            elif xform != "normalize_phone":
                issues.add(
                    _REVIEW,
                    "phone_not_normalized",
                    "Phone values usually need normalize_phone to pass CMS validation.",
                )
        case DataType.EMAIL | DataType.DATE | DataType.NUMBER:
            if s in free_text:
                issues.add(
                    _REVIEW,
                    "free_text_conversion",
                    f"Free text may not be valid {t}; values will be rejected individually.",
                )
            else:
                issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")
        case DataType.ENUM:
            if s in free_text:
                issues.add(
                    _REVIEW,
                    "free_text_to_enum",
                    "Free text only works if users type exactly one of the target options.",
                )
            elif s is not DataType.ENUM:
                issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")
        case _:
            issues.add(_INVALID, "incompatible_types", f"{s} cannot be stored as {t}.")


def _check_value_map(
    p: ProposedMapping, src: NormalizedField, tgt: NormalizedField, issues: _Issues
) -> None:
    if tgt.data_type is not DataType.ENUM:
        if p.value_map:
            issues.add(
                _INVALID, "value_map_requires_enum_target", "Only enum targets take a value map."
            )
        return
    target_options = set(tgt.options or [])
    keys = Counter(e.source_value for e in p.value_map)
    if any(n > 1 for n in keys.values()):
        issues.add(_INVALID, "duplicate_value_map_entry", "A source value is mapped twice.")
    bad_targets = sorted({e.target_value for e in p.value_map} - target_options)
    if bad_targets:
        issues.add(
            _INVALID,
            "invalid_enum_option",
            f"Not options of '{tgt.key}': {', '.join(bad_targets)}.",
        )
    if src.data_type is not DataType.ENUM:
        return
    preview = _PREVIEW.get(p.transformation, _identity)
    mapping = {e.source_value: e.target_value for e in p.value_map}
    transformed_options = {preview(o) for o in src.options or []}
    unknown = sorted(set(mapping) - transformed_options)
    if unknown:
        issues.add(
            _INVALID,
            "unknown_source_option",
            f"Value map keys are not source options after '{p.transformation}': "
            f"{', '.join(unknown)}.",
        )
    unmapped = sorted(
        o for o in src.options or [] if mapping.get(preview(o), preview(o)) not in target_options
    )
    if unmapped:
        issues.add(
            _REVIEW,
            "unmapped_source_options",
            f"These source options would be rejected by the CMS: {', '.join(unmapped)}.",
        )


def _check_map(
    p: ProposedMapping, src: NormalizedField, target: NormalizedSchema, issues: _Issues
) -> None:
    tgt = target.field(p.target_field) if p.target_field else None
    if tgt is None:
        issues.add(
            _INVALID,
            "unknown_target_field",
            f"Target field '{p.target_field}' does not exist in the target schema.",
        )
        return
    if tgt.canonical_ref is not None and tgt.canonical_ref in SYSTEM_MANAGED_CANONICAL:
        issues.add(
            _INVALID,
            "unauthorized_configuration_area",
            f"'{tgt.key}' is managed by the CMS workflow and cannot be set by intake mappings.",
        )
    supported = target.supported_transformations or {}
    if p.transformation not in supported:
        issues.add(
            _INVALID,
            "unsupported_transformation",
            f"Transformation '{p.transformation}' is not supported by the target.",
        )
    elif tgt.data_type not in supported[p.transformation]:
        issues.add(
            _INVALID,
            "transformation_incompatible",
            f"'{p.transformation}' cannot produce {tgt.data_type} values.",
        )
    _check_types(src, tgt, issues, p.transformation)
    _check_value_map(p, src, tgt, issues)
    if p.confidence < LOW_CONFIDENCE:
        issues.add(_REVIEW, "low_confidence", f"Model confidence {p.confidence:.2f} is low.")


def _check_custom_field(p: ProposedMapping, target: NormalizedSchema, issues: _Issues) -> None:
    issues.add(_REVIEW, "custom_field_creation", "Creating a CMS field needs human approval.")
    entities = {f.entity for f in target.fields if f.entity}
    if p.custom_field_entity not in entities:
        issues.add(_INVALID, "invalid_custom_field", f"Unknown entity '{p.custom_field_entity}'.")
    if not CUSTOM_FIELD_NAME.match(p.custom_field_name):
        issues.add(_INVALID, "invalid_custom_field", "Custom field name must be snake_case.")
    if p.custom_field_data_type not in {t.value for t in CREATABLE_TYPES}:
        issues.add(
            _INVALID, "invalid_custom_field", f"Unknown data type '{p.custom_field_data_type}'."
        )
    is_enum = p.custom_field_data_type == DataType.ENUM
    if is_enum != bool(p.custom_field_options):
        issues.add(
            _INVALID, "invalid_custom_field", "Enum custom fields need options; others must not."
        )
    if target.field(f"{p.custom_field_entity}.custom.{p.custom_field_name}") is not None:
        issues.add(
            _REVIEW, "custom_field_exists", "That custom field already exists; map to it instead."
        )


def _validate_one(
    p: ProposedMapping,
    src: NormalizedField,
    target: NormalizedSchema,
    untrusted: set[str],
    issues: _Issues,
) -> None:
    match p.decision:
        case Decision.MAP:
            _check_map(p, src, target, issues)
        case Decision.AMBIGUOUS:
            issues.add(_REVIEW, "ambiguous_mapping", "The model could not choose a single target.")
            unknown = [c for c in p.candidates if target.field(c) is None]
            if unknown:
                issues.add(
                    _REVIEW, "unknown_candidate", f"Not target fields: {', '.join(unknown)}."
                )
        case Decision.REQUIRES_CUSTOM_FIELD:
            _check_custom_field(p, target, issues)
        case Decision.UNSUPPORTED:
            if src.data_type is not DataType.UNSUPPORTED:
                issues.add(
                    _REVIEW,
                    "unsupported_declared",
                    f"'{src.key}' has type {src.data_type}, which intake mappings support.",
                )
        case Decision.NO_TARGET:
            pass
    if p.decision is not Decision.MAP and src.required:
        issues.add(
            _REVIEW,
            "required_source_unmapped",
            f"'{src.key}' is required by the firm but would not reach the CMS.",
        )
    if src.key in untrusted:
        issues.add(
            _REVIEW,
            "untrusted_instruction_text",
            "The field's text contains instruction-like content; treat it as data and review.",
        )


def validate_proposals(
    proposals: list[ProposedMapping],
    source: NormalizedSchema,
    target: NormalizedSchema,
    untrusted_fields: set[str] | None = None,
) -> ValidationReport:
    """Validate proposals in order; the report's `mappings` align with `proposals` one-to-one."""
    untrusted = untrusted_fields or set()
    results: list[MappingValidation] = []
    seen: set[str] = set()
    for p in proposals:
        issues = _Issues()
        src = source.field(p.source_field)
        if src is None:
            issues.add(
                _INVALID,
                "unknown_source_field",
                f"'{p.source_field}' is not a field of the source schema.",
            )
        elif p.source_field in seen:
            issues.add(_INVALID, "duplicate_source_field", "This source field was proposed twice.")
        else:
            seen.add(p.source_field)
            _validate_one(p, src, target, untrusted, issues)
        results.append(
            MappingValidation(
                source_field=p.source_field, status=issues.status(), issues=issues.items
            )
        )

    # A target field can receive at most one source field.
    usable = [
        (p, r)
        for p, r in zip(proposals, results, strict=True)
        if p.decision is Decision.MAP and r.status is not _INVALID
    ]
    targets = Counter(p.target_field for p, _ in usable)
    for p, r in usable:
        if targets[p.target_field] > 1:
            r.issues.append(
                ValidationIssue(
                    code="duplicate_target",
                    message=f"Several source fields map to '{p.target_field}'.",
                    severity=_REVIEW,
                )
            )
            r.status = _REVIEW

    covered = {p.target_field for p, _ in usable}
    gaps = [
        CoverageGap(
            target_field=f.key,
            code="required_target_unmapped",
            message=f"Required CMS field '{f.key}' receives no source field.",
        )
        for f in target.fields
        if f.required and f.key not in covered
    ]
    return ValidationReport(
        mappings=results,
        coverage_gaps=gaps,
        unproposed_source_fields=[f.key for f in source.fields if f.key not in seen],
    )
