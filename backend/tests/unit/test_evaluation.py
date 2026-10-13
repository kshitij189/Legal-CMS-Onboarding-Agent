"""The evaluation suite's own logic: scoring rules, record/replay, scenario integrity."""

from pathlib import Path

import pytest

from app.agent.llm_client import LLMError
from app.agent.tool_registry import PROPOSE_FIELD_MAPPINGS
from app.evaluation.llm_cache import CachedLLM
from app.evaluation.mapping_eval import (
    FieldLabel,
    _score_field,
    load_mapping_scenarios,
    load_validation_scenarios,
    run_validation_scenario,
)
from app.evaluation.simulation import ScriptedLLM
from app.schemas.mapping import ProposedMapping, ValidationStatus


def _p(decision: str = "map", target: str = "contact.email") -> ProposedMapping:
    return ProposedMapping(
        source_field="f", decision=decision, target_field=target, confidence=0.9, rationale="r"
    )


VALID, REVIEW, INVALID = (
    ValidationStatus.VALID,
    ValidationStatus.NEEDS_REVIEW,
    ValidationStatus.INVALID,
)


@pytest.mark.parametrize(
    ("label", "proposal", "status", "correct", "unsafe"),
    [
        # clearly mappable: only the exact target counts; a valid wrong target is unsafe
        (
            FieldLabel(source_field="f", expect="map", target_field="contact.email"),
            _p(),
            VALID,
            True,
            False,
        ),
        (
            FieldLabel(source_field="f", expect="map", target_field="contact.email"),
            _p(target="contact.full_name"),
            VALID,
            False,
            True,
        ),
        (
            FieldLabel(source_field="f", expect="map", target_field="contact.email"),
            _p(decision="ambiguous", target=""),
            REVIEW,
            False,
            False,
        ),
        # needs a human: anything but a *valid* mapping
        (FieldLabel(source_field="f", expect="review"), _p(), REVIEW, True, False),
        (FieldLabel(source_field="f", expect="review"), _p(), VALID, False, True),
        # must not reach the CMS: not mapped, or rejected by the validator
        (
            FieldLabel(source_field="f", expect="not_mapped"),
            _p(decision="no_target", target=""),
            VALID,
            True,
            False,
        ),
        (FieldLabel(source_field="f", expect="not_mapped"), _p(), INVALID, True, False),
        (FieldLabel(source_field="f", expect="not_mapped"), _p(), VALID, False, True),
        # the model skipped the field entirely
        (
            FieldLabel(source_field="f", expect="map", target_field="contact.email"),
            None,
            None,
            False,
            False,
        ),
    ],
)
def test_scoring_rules(
    label: FieldLabel,
    proposal: ProposedMapping | None,
    status: ValidationStatus | None,
    correct: bool,
    unsafe: bool,
) -> None:
    scored = _score_field(label, proposal, status)
    assert (scored["correct"], scored["unsafe"]) == (correct, unsafe)


def test_record_then_replay_returns_the_same_answer(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    live = CachedLLM(cache, "live", ScriptedLLM([_p()]))
    recorded = live.call_tool("system", "user", PROPOSE_FIELD_MAPPINGS)
    replay = CachedLLM(cache, "replay")
    replayed = replay.call_tool("system", "user", PROPOSE_FIELD_MAPPINGS)
    assert replayed.arguments == recorded.arguments
    assert (replayed.provider, replayed.model) == ("scripted", "scripted")
    with pytest.raises(LLMError) as exc:  # a different prompt was never recorded
        replay.call_tool("system", "changed prompt", PROPOSE_FIELD_MAPPINGS)
    assert exc.value.category == "configuration"


def test_scenario_suite_is_complete_and_consistent() -> None:
    mapping, validation = load_mapping_scenarios(), load_validation_scenarios()
    assert len(mapping) == 18 and len(validation) == 12
    for scenario in mapping:
        source_keys = {f.key for f in scenario.source.load().fields}
        labelled = [label.source_field for label in scenario.all_labels()]
        assert sorted(labelled) == sorted(source_keys), scenario.id  # every field labelled once
        target_keys = {f.key for f in scenario.target().fields}
        assert all(
            label.target_field in target_keys
            for label in scenario.all_labels()
            if label.target_field
        ), scenario.id
    assert all(run_validation_scenario(s)["passed"] for s in validation)
