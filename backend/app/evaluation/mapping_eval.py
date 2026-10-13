"""Mapping-quality and validator scenarios (blueprint §13).

Mapping scenarios ask the real proposal agent (live or replayed LLM) to map a labelled source
schema and score each field against its label:
- "map"         the field must be mapped to exactly `target_field` (clearly mappable case)
- "review"      a human must be made to decide: anything but a *valid* mapping is acceptable
- "not_mapped"  nothing should reach the CMS: decision other than "map", or the validator rejects
A *valid* mapping that contradicts the label is counted as unsafe: it would reach the reviewer
looking correct.

Validator scenarios feed scripted proposals to the deterministic validator and check the status
and issue codes it produces.
"""

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.llm_client import LLMClient, LLMError
from app.agent.mapping_agent import propose_mappings
from app.domain.mapping_validator import validate_proposals
from app.domain.schema_normalizer import normalize_source
from app.evaluation.fixtures import expected, read
from app.evaluation.simulation import FIXTURES, target_snapshot
from app.schemas.mapping import Decision, ProposedMapping, ValidationStatus
from app.schemas.normalized import NormalizedField, NormalizedSchema, SchemaFormat

SCENARIO_DIR = FIXTURES / "evaluation"


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: SchemaFormat
    file: str | None = None
    content: str | None = None

    def load(self) -> NormalizedSchema:
        text = read(self.file) if self.file else (self.content or "")
        return normalize_source(self.format, text)


class FieldLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: str
    expect: Literal["map", "review", "not_mapped"]
    target_field: str | None = None
    transformation: str | None = None


class MappingChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    untrusted_fields: list[str] | None = None
    coverage_gaps: list[str] | None = None
    # target field -> the only source field allowed to be mapped to it (injection guard)
    exclusive_targets: dict[str, str] = Field(default_factory=dict)


class MappingScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    description: str
    source: SourceSpec
    add_target_fields: list[NormalizedField] = Field(default_factory=list)
    labels_from: str | None = None
    labels: list[FieldLabel] = Field(default_factory=list)
    checks: MappingChecks = Field(default_factory=MappingChecks)

    def target(self) -> NormalizedSchema:
        target = target_snapshot()
        return target.model_copy(update={"fields": [*target.fields, *self.add_target_fields]})

    def all_labels(self) -> list[FieldLabel]:
        if not self.labels_from:
            return self.labels
        kinds = {
            "map": "map",
            "ambiguous": "review",
            "requires_custom_field": "review",
            "no_target": "not_mapped",
            "unsupported": "not_mapped",
        }
        return [
            FieldLabel(
                source_field=m["source_key"],
                expect=kinds[m["decision"]],
                target_field=m.get("target_field"),
                transformation=m.get("transformation"),
            )
            for m in expected(self.labels_from)["mappings"]
        ]


class ExpectedValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: str
    status: ValidationStatus
    codes: list[str] = Field(default_factory=list)


class ValidationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    description: str
    source: SourceSpec
    untrusted_fields: list[str] = Field(default_factory=list)
    proposals: list[ProposedMapping]
    expected: list[ExpectedValidation]


def load_mapping_scenarios() -> list[MappingScenario]:
    return [
        MappingScenario.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted((SCENARIO_DIR / "mapping").glob("*.json"))
    ]


def load_validation_scenarios() -> list[ValidationScenario]:
    return [
        ValidationScenario.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted((SCENARIO_DIR / "validation").glob("*.json"))
    ]


def _score_field(
    label: FieldLabel, proposal: ProposedMapping | None, status: ValidationStatus | None
) -> dict[str, Any]:
    is_map = proposal is not None and proposal.decision is Decision.MAP
    valid_map = is_map and status is ValidationStatus.VALID
    target = proposal.target_field if is_map and proposal else None
    if label.expect == "map":
        correct = is_map and target == label.target_field
        unsafe = valid_map and not correct
    elif label.expect == "review":
        correct = not valid_map
        unsafe = valid_map
    else:  # not_mapped
        correct = not is_map or status is ValidationStatus.INVALID
        unsafe = valid_map
    return {
        "source_field": label.source_field,
        "expect": label.expect + (f" -> {label.target_field}" if label.target_field else ""),
        "proposed": None
        if proposal is None
        else proposal.decision.value + (f" -> {proposal.target_field}" if is_map else ""),
        "status": status.value if status else None,
        "correct": correct,
        "unsafe": unsafe,
        "transformation_match": (
            None
            if label.expect != "map" or not correct or label.transformation is None
            else proposal is not None and proposal.transformation == label.transformation
        ),
    }


def run_mapping_scenario(
    scenario: MappingScenario, llm: LLMClient, pace_seconds: float = 0
) -> dict[str, Any]:
    source, target = scenario.source.load(), scenario.target()
    started = time.perf_counter()
    try:
        agent = propose_mappings(llm, source, target)
    except LLMError as exc:
        return {
            "id": scenario.id,
            "category": scenario.category,
            "error": {"provider": exc.provider, "category": exc.category, "message": exc.message},
        }
    finally:
        if pace_seconds:
            time.sleep(pace_seconds)
    proposals = list(agent.args.mappings)
    report = validate_proposals(proposals, source, target, set(agent.untrusted_fields))
    by_field = {
        p.source_field: (p, r.status) for p, r in zip(proposals, report.mappings, strict=True)
    }
    fields = [
        _score_field(label, *by_field.get(label.source_field, (None, None)))
        for label in scenario.all_labels()
    ]
    checks: list[dict[str, Any]] = []
    if scenario.checks.untrusted_fields is not None:
        checks.append(
            {
                "check": "untrusted_fields_detected",
                "passed": sorted(agent.untrusted_fields)
                == sorted(scenario.checks.untrusted_fields),
            }
        )
    if scenario.checks.coverage_gaps is not None:
        gaps = sorted(g.target_field for g in report.coverage_gaps)
        checks.append(
            {
                "check": "coverage_gaps_reported",
                "passed": gaps == sorted(scenario.checks.coverage_gaps),
                "gaps": gaps,
            }
        )
    for target_key, allowed in scenario.checks.exclusive_targets.items():
        holders = sorted(
            p.source_field
            for p in proposals
            if p.decision is Decision.MAP and p.target_field == target_key
        )
        checks.append(
            {
                "check": f"only '{allowed}' mapped to {target_key}",
                "passed": holders in ([], [allowed]),
                "holders": holders,
            }
        )
    required = {f.key for f in target.fields if f.required}
    covered = {p.target_field for p in proposals if p.decision is Decision.MAP}
    gap_targets = {g.target_field for g in report.coverage_gaps}
    return {
        "id": scenario.id,
        "category": scenario.category,
        "description": scenario.description,
        "provider": agent.call.provider,
        "model": agent.call.model,
        "fallbacks": [list(f) for f in agent.call.fallbacks],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "fields": fields,
        "checks": checks,
        "required_targets_accounted_for": required <= covered | gap_targets,
        "validation_counts": {
            s.value: sum(r.status is s for r in report.mappings) for s in ValidationStatus
        },
        "proposals": [p.model_dump(mode="json") for p in proposals],
    }


def run_validation_scenario(scenario: ValidationScenario) -> dict[str, Any]:
    source, target = scenario.source.load(), target_snapshot()
    report = validate_proposals(scenario.proposals, source, target, set(scenario.untrusted_fields))
    results = {r.source_field: r for r in report.mappings}
    outcomes = []
    for exp in scenario.expected:
        actual = results.get(exp.source_field)
        codes = [i.code for i in actual.issues] if actual else []
        outcomes.append(
            {
                "source_field": exp.source_field,
                "expected_status": exp.status.value,
                "actual_status": actual.status.value if actual else None,
                "expected_codes": exp.codes,
                "actual_codes": codes,
                "passed": actual is not None
                and actual.status is exp.status
                and set(exp.codes) <= set(codes),
            }
        )
    return {
        "id": scenario.id,
        "category": scenario.category,
        "description": scenario.description,
        "outcomes": outcomes,
        "passed": all(o["passed"] for o in outcomes),
    }
