"""Deterministic stand-ins used by the evaluation suite and the test suite.

- `InMemoryCMS`: a reader/writer over an in-memory CMS configuration with fault injection.
- `ScriptedLLM`: an LLM client that returns fixed proposals (or a fixed error).

They exercise the real execution and validation code; only the external systems are simulated.
"""

import json
from pathlib import Path
from typing import Any

from app.agent.llm_client import LLMError, ToolCallResult
from app.agent.tool_registry import ToolSpec
from app.config import REPO_ROOT
from app.errors import ConnectorError
from app.schemas.execution import (
    CMSConfiguration,
    CMSField,
    CustomFieldSpec,
    MappingSpec,
    WriteOutcome,
)
from app.schemas.mapping import ProposedMapping, ProposeFieldMappingsArgs
from app.schemas.normalized import NormalizedSchema

FIXTURES = REPO_ROOT / "fixtures"


def target_snapshot() -> NormalizedSchema:
    raw = json.loads(
        (FIXTURES / "target-schemas" / "mock-cms-default.json").read_text(encoding="utf-8")
    )
    raw.pop("_comment", None)
    return NormalizedSchema.model_validate(raw)


def configuration_from(schema: NormalizedSchema) -> CMSConfiguration:
    """A CMS holding exactly the given target schema and no intake mappings."""
    return CMSConfiguration(
        config_revision=0,
        fields=[
            CMSField(
                key=f.key,
                entity=f.entity or "",
                label=f.label,
                data_type=f.data_type.value,
                required=f.required,
                enabled=True,
                is_custom=".custom." in f.key,
                options=f.options,
            )
            for f in schema.fields
        ],
        mappings=[],
    )


def default_configuration() -> CMSConfiguration:
    return configuration_from(target_snapshot())


class SimulatedCrash(BaseException):  # noqa: N818 - models a process dying, not an error
    """Escapes every `except Exception` handler, like a killed process would."""


class InMemoryCMS:
    """Reader and writer over one shared in-memory configuration.

    faults: subject -> list of fault names, consumed one per write attempt:
      "uncertain_applied"  apply the change, then report `uncertain` (response lost)
      "uncertain_lost"     do not apply, report `uncertain`
      "validation"         reject with `validation`
      "ui_drift"           fail with `ui_drift` (a renamed control)
      "crash_after_apply"  apply the change, then die before anyone can record it
      "store_different"    report success but store a different value (silent corruption)
    """

    def __init__(
        self,
        faults: dict[str, list[str]] | None = None,
        config: CMSConfiguration | None = None,
    ) -> None:
        self.config = config or default_configuration()
        self.faults = faults or {}
        self.writes: list[str] = []
        self.reads = 0
        self.saved_artifacts: list[str] = []
        self.blocked_requests: list[str] = []

    def read_configuration(self) -> CMSConfiguration:
        self.reads += 1
        return self.config.model_copy(deep=True)

    def __enter__(self) -> "InMemoryCMS":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def _fault(self, subject: str) -> str | None:
        queue = self.faults.get(subject, [])
        return queue.pop(0) if queue else None

    def _after_apply(self, fault: str | None) -> WriteOutcome:
        self.config.config_revision += 1
        if fault == "uncertain_applied":
            raise ConnectorError("uncertain", "injected lost response")
        if fault == "crash_after_apply":
            raise SimulatedCrash
        return WriteOutcome(changed=True, evidence={"channel": "simulated"})

    def create_custom_field(self, spec: CustomFieldSpec) -> WriteOutcome:
        self.writes.append(spec.key)
        fault = self._fault(spec.key)
        if fault in ("validation", "ui_drift", "uncertain_lost"):
            raise ConnectorError(fault.replace("_lost", ""), f"injected {fault}")
        if any(f.key == spec.key for f in self.config.fields):
            raise ConnectorError("conflict", "Field already exists.")  # what a real CMS does
        self.config.fields.append(
            CMSField(
                key=spec.key,
                entity=spec.entity,
                label=spec.label,
                data_type=spec.data_type,
                required=spec.required,
                enabled=True,
                is_custom=True,
                options=spec.options,
            )
        )
        return self._after_apply(fault)

    def upsert_mapping(self, spec: MappingSpec) -> WriteOutcome:
        self.writes.append(spec.source_key)
        fault = self._fault(spec.source_key)
        if fault in ("validation", "ui_drift", "uncertain_lost"):
            raise ConnectorError(fault.replace("_lost", ""), f"injected {fault}")
        stored = spec
        if fault == "store_different":
            stored = spec.model_copy(update={"transformation": "none"})
        self.config.mappings = [
            m for m in self.config.mappings if m.source_key != spec.source_key
        ] + [stored]
        return self._after_apply(fault)

    def reader_factory(self, connector: Any) -> "InMemoryCMS":
        return self

    def writer_factory(self, connector: Any, channel: str, artifacts_dir: Path) -> "InMemoryCMS":
        return self


class ScriptedLLM:
    """Returns fixed proposals (or raises a fixed error). Records every call."""

    provider = "scripted"
    model = "scripted"

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
