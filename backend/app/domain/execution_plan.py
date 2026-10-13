"""Pure logic for execution: build the plan, check drift, compare expected with actual.

No I/O here. The plan is derived only from an approved mapping version, so nothing that was not
approved can reach the CMS.
"""

from typing import Any

from app.schemas.execution import (
    ActionType,
    CMSConfiguration,
    CustomFieldSpec,
    DriftIssue,
    MappingSpec,
    PlannedAction,
    VerificationCheck,
)
from app.schemas.mapping import Decision, ProposedMapping
from app.schemas.normalized import NormalizedSchema


def build_plan(items: list[dict[str, Any]], source: NormalizedSchema) -> list[PlannedAction]:
    """Custom fields first (mappings may target them), then mappings, in a stable order."""
    fields: list[CustomFieldSpec] = []
    mappings: list[MappingSpec] = []
    for item in items:
        p = ProposedMapping.model_validate(item["mapping"])
        if p.decision is Decision.REQUIRES_CUSTOM_FIELD:
            src = source.field(p.source_field)
            spec = CustomFieldSpec(
                entity=p.custom_field_entity,
                name=p.custom_field_name,
                label=src.label if src else p.custom_field_name,
                data_type=p.custom_field_data_type,
                required=bool(src and src.required),
                options=p.custom_field_options or None,
            )
            fields.append(spec)
            mappings.append(
                MappingSpec(source_key=p.source_field, target_field=spec.key, transformation="none")
            )
        elif p.decision is Decision.MAP:
            mappings.append(
                MappingSpec(
                    source_key=p.source_field,
                    target_field=p.target_field,
                    transformation=p.transformation,
                    value_map={e.source_value: e.target_value for e in p.value_map} or None,
                )
            )
    actions = [
        PlannedAction(
            sequence=0, action_type=ActionType.CREATE_CUSTOM_FIELD, subject=f.key, custom_field=f
        )
        for f in sorted(fields, key=lambda f: f.key)
    ] + [
        PlannedAction(
            sequence=0,
            action_type=ActionType.UPSERT_INTAKE_MAPPING,
            subject=m.source_key,
            mapping=m,
        )
        for m in sorted(mappings, key=lambda m: m.source_key)
    ]
    return [a.model_copy(update={"sequence": i + 1}) for i, a in enumerate(actions)]


def _field_matches(spec: CustomFieldSpec, config: CMSConfiguration) -> tuple[bool, str]:
    actual = config.field(spec.key)
    if actual is None:
        return False, "absent"
    differences = [
        name
        for name, ok in (
            ("data_type", actual.data_type == spec.data_type),
            ("required", actual.required == spec.required),
            ("options", (actual.options or None) == (spec.options or None)),
            ("enabled", actual.enabled),
        )
        if not ok
    ]
    return (not differences), ", ".join(differences) or "matches"


def action_state(action: PlannedAction, config: CMSConfiguration) -> str:
    """'done' if the CMS already holds the desired state, 'todo' if a write is needed, or
    'conflict' if the write cannot succeed without touching something outside the plan."""
    if action.custom_field is not None:
        if config.field(action.subject) is None:
            return "todo"
        matches, _ = _field_matches(action.custom_field, config)
        return "done" if matches else "conflict"
    assert action.mapping is not None  # noqa: S101 - PlannedAction invariant
    current = config.mapping(action.mapping.source_key)
    if current == action.mapping:
        return "done"
    holder = next(
        (m for m in config.mappings if m.target_field == action.mapping.target_field), None
    )
    if holder is not None and holder.source_key != action.mapping.source_key:
        return "conflict"  # the target is mapped from a source key this plan does not manage
    return "todo"


def check_drift(
    pinned: NormalizedSchema, live: CMSConfiguration, plan: list[PlannedAction]
) -> list[DriftIssue]:
    """Compare the target schema the approval was based on with the live CMS.

    Changes to fields the plan writes to block execution; other differences are reported only.
    Fields this plan creates itself (custom fields from an earlier, partial run) are expected.
    """
    planned_fields = {a.subject for a in plan if a.custom_field is not None}
    targeted = {a.mapping.target_field for a in plan if a.mapping is not None}
    issues: list[DriftIssue] = []
    for field in pinned.fields:
        actual = live.field(field.key)
        blocking = field.key in targeted
        if actual is None or not actual.enabled:
            issues.append(
                DriftIssue(
                    field=field.key,
                    code="field_removed",
                    blocking=blocking,
                    message=f"'{field.key}' is no longer available in the CMS.",
                )
            )
            continue
        changed = [
            name
            for name, ok in (
                ("data_type", actual.data_type == field.data_type.value),
                ("required", actual.required == field.required),
                ("options", (actual.options or None) == (field.options or None)),
            )
            if not ok
        ]
        if changed:
            issues.append(
                DriftIssue(
                    field=field.key,
                    code="field_changed",
                    blocking=blocking,
                    message=f"'{field.key}' changed since approval: {', '.join(changed)}.",
                )
            )
    pinned_keys = {f.key for f in pinned.fields}
    for live_field in live.fields:
        key = live_field.key
        if live_field.enabled and key not in pinned_keys and key not in planned_fields:
            issues.append(
                DriftIssue(
                    field=key,
                    code="unexpected_field",
                    blocking=False,
                    message=f"'{key}' exists in the CMS but was not in the approved target "
                    "schema; it is preserved and left untouched.",
                )
            )
    return issues


def verify(plan: list[PlannedAction], config: CMSConfiguration) -> list[VerificationCheck]:
    """Read-after-write comparison of every planned action with the stored configuration."""
    checks: list[VerificationCheck] = []
    for action in plan:
        if action.custom_field is not None:
            spec = action.custom_field
            actual_field = config.field(spec.key)
            matches, detail = _field_matches(spec, config)
            checks.append(
                VerificationCheck(
                    subject=spec.key,
                    check_type="custom_field",
                    expected=spec.model_dump(mode="json"),
                    actual=actual_field.model_dump(mode="json") if actual_field else None,
                    status="match"
                    if matches
                    else ("missing" if actual_field is None else "mismatch"),
                    reason="stored as approved" if matches else f"differs: {detail}",
                )
            )
        else:
            assert action.mapping is not None  # noqa: S101
            actual_mapping = config.mapping(action.mapping.source_key)
            if actual_mapping is None:
                status, reason = "missing", "no mapping stored for this source key"
            elif actual_mapping == action.mapping:
                status, reason = "match", "stored as approved"
            else:
                diffs = [
                    k
                    for k in ("target_field", "transformation", "value_map")
                    if getattr(actual_mapping, k) != getattr(action.mapping, k)
                ]
                status, reason = "mismatch", f"differs: {', '.join(diffs)}"
            checks.append(
                VerificationCheck(
                    subject=action.mapping.source_key,
                    check_type="mapping",
                    expected=action.mapping.model_dump(mode="json"),
                    actual=actual_mapping.model_dump(mode="json") if actual_mapping else None,
                    status=status,
                    reason=reason,
                )
            )
    return checks
