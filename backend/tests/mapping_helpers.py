"""Shared helpers: ground-truth fixtures as proposals, normalized fixture schemas, a fake LLM."""

import json
from typing import Any

from app.agent.llm_client import LLMError, ToolCallResult
from app.agent.tool_registry import ToolSpec
from app.domain.schema_normalizer import normalize_source
from app.schemas.mapping import ProposedMapping, ProposeFieldMappingsArgs
from app.schemas.normalized import NormalizedSchema, SchemaFormat
from tests.conftest import FIXTURES, SOURCE_FILES, read_fixture


def source_schema(firm: str) -> NormalizedSchema:
    fmt, path = SOURCE_FILES[firm]
    return normalize_source(SchemaFormat(fmt), read_fixture(path))


def target_schema() -> NormalizedSchema:
    raw = json.loads(read_fixture(FIXTURES / "target-schemas" / "mock-cms-default.json"))
    raw.pop("_comment")
    return NormalizedSchema.model_validate(raw)


def expected(firm: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(read_fixture(FIXTURES / "expected-mappings" / f"{firm}.json"))
    return data


def ground_truth_proposals(firm: str) -> list[ProposedMapping]:
    """The labelled answers expressed as the LLM tool's output (what a perfect model returns)."""
    proposals = []
    for m in expected(firm)["mappings"]:
        custom = m.get("proposed_custom_field") or {}
        proposals.append(
            ProposedMapping(
                source_field=m["source_key"],
                decision=m["decision"],
                target_field=m.get("target_field", ""),
                transformation=m.get("transformation", "none"),
                value_map=[
                    {"source_value": k, "target_value": v}
                    for k, v in (m.get("value_map") or {}).items()
                ],
                candidates=m.get("candidates", []),
                custom_field_entity=custom.get("entity", ""),
                custom_field_name=custom.get("name", ""),
                custom_field_data_type=custom.get("data_type", ""),
                custom_field_options=custom.get("options", []),
                confidence=0.9,
                rationale=m.get("reason", "Matches the field definition."),
            )
        )
    return proposals


class FakeLLM:
    """Deterministic stand-in for a provider. Records calls; can be scripted to fail."""

    provider = "fake"
    model = "fake-model"

    def __init__(
        self, proposals: list[ProposedMapping] | None = None, error: LLMError | None = None
    ) -> None:
        self.proposals = proposals or []
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def call_tool(self, system: str, user: str, tool: ToolSpec) -> ToolCallResult:
        self.calls.append((system, user, tool.name))
        if self.error is not None:
            raise self.error
        return ToolCallResult(
            arguments=ProposeFieldMappingsArgs(mappings=self.proposals),
            provider=self.provider,
            model=self.model,
            input_tokens=1000,
            output_tokens=500,
            latency_ms=5,
        )
