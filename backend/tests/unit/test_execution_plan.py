"""Plan building, drift detection and read-after-write comparison (pure functions)."""

from typing import Any

from app.domain.execution_plan import action_state, build_plan, check_drift, verify
from app.schemas.execution import ActionType, MappingSpec
from tests.execution_helpers import default_configuration
from tests.mapping_helpers import ground_truth_proposals, source_schema, target_schema


def _items(firm: str) -> list[dict[str, Any]]:
    """Approved-version items as the approval step stores them (map + custom-field decisions)."""
    return [
        {"proposal_id": i, "mapping": p.model_dump(mode="json")}
        for i, p in enumerate(ground_truth_proposals(firm))
        if p.decision.value in ("map", "requires_custom_field")
    ]


def test_plan_creates_custom_fields_first_then_mappings() -> None:
    plan = build_plan(_items("firm-c"), source_schema("firm-c"))
    assert [a.sequence for a in plan] == list(range(1, len(plan) + 1))
    assert plan[0].action_type is ActionType.CREATE_CUSTOM_FIELD
    field = plan[0].custom_field
    assert field is not None
    assert (field.key, field.required, field.options, field.label) == (
        "matter.custom.review_status",
        True,
        ["pending", "reviewed", "escalated"],
        "Review status",
    )
    review = next(a for a in plan if a.subject == "review_state")
    assert review.mapping == MappingSpec(
        source_key="review_state", target_field="matter.custom.review_status", transformation="none"
    )
    area = next(a for a in plan if a.subject == "area_of_law")
    assert area.mapping is not None and area.mapping.value_map == {
        "DIV": "family_law",
        "WRK": "employment",
        "IMM": "immigration",
        "EST": "estate_planning",
    }
    # Only approved map/custom-field items become actions.
    assert {a.subject for a in plan} >= {"pc_name", "pc_tel"} and "urgent" not in {
        a.subject for a in plan
    }


def test_action_state() -> None:
    config = default_configuration()
    plan = build_plan(_items("firm-c"), source_schema("firm-c"))
    mapping = next(a for a in plan if a.subject == "pc_mail")
    assert action_state(mapping, config) == "todo"
    assert mapping.mapping is not None
    config.mappings.append(mapping.mapping)
    assert action_state(mapping, config) == "done"
    # Another source key already holds the target: never overwrite unmanaged configuration.
    config.mappings = [mapping.mapping.model_copy(update={"source_key": "legacy_email"})]
    assert action_state(mapping, config) == "conflict"
    field = plan[0]
    assert action_state(field, config) == "todo"


def test_drift_detection() -> None:
    pinned = target_schema()
    plan = build_plan(_items("firm-a"), source_schema("firm-a"))
    live = default_configuration()
    assert check_drift(pinned, live, plan) == []

    phone = live.field("contact.phone_number")
    assert phone is not None
    phone.data_type = "string"  # a targeted field changed type
    intake = live.field("matter.intake_status")
    assert intake is not None
    intake.required = True  # an untargeted field changed
    live.fields.append(live.fields[0].model_copy(update={"key": "contact.custom.extra"}))
    issues = {i.field: i for i in check_drift(pinned, live, plan)}
    assert issues["contact.phone_number"].blocking is True
    assert issues["matter.intake_status"].blocking is False
    assert issues["contact.custom.extra"].code == "unexpected_field"
    assert issues["contact.custom.extra"].blocking is False


def test_fields_created_by_the_plan_are_not_drift() -> None:
    plan = build_plan(_items("firm-c"), source_schema("firm-c"))
    live = default_configuration()
    live.fields.append(live.fields[0].model_copy(update={"key": "matter.custom.review_status"}))
    assert check_drift(target_schema(), live, plan) == []


def test_verify_reports_match_mismatch_missing() -> None:
    plan = build_plan(_items("firm-a"), source_schema("firm-a"))
    config = default_configuration()
    config.mappings = [a.mapping for a in plan if a.mapping is not None]
    assert {c.status for c in verify(plan, config)} == {"match"}

    email = next(m for m in config.mappings if m.source_key == "client_email")
    config.mappings.remove(email)
    config.mappings.append(email.model_copy(update={"transformation": "none"}))
    config.mappings = [m for m in config.mappings if m.source_key != "jurisdiction"]
    checks = {c.subject: c for c in verify(plan, config)}
    assert checks["client_email"].status == "mismatch"
    assert "transformation" in checks["client_email"].reason
    assert checks["jurisdiction"].status == "missing"
    assert checks["client_mobile"].status == "match"
