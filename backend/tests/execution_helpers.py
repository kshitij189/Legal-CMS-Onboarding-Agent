"""An in-memory CMS for execution tests, with fault injection."""

from pathlib import Path
from typing import Any

from app.errors import ConnectorError
from app.schemas.execution import (
    CMSConfiguration,
    CMSField,
    CustomFieldSpec,
    MappingSpec,
    WriteOutcome,
)
from tests.mapping_helpers import target_schema


def default_configuration() -> CMSConfiguration:
    """The CMS as captured in the target snapshot, with no intake mappings."""
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
                is_custom=False,
                options=f.options,
            )
            for f in target_schema().fields
        ],
        mappings=[],
    )


class FakeCMS:
    """Reader and writer over one shared in-memory configuration.

    faults: subject -> list of fault names consumed one per write attempt:
      "uncertain_applied"   apply the change, then raise `uncertain` (response lost)
      "uncertain_lost"      do not apply, raise `uncertain`
      "validation"          raise `validation`
      "ui_drift"            raise `ui_drift`
    """

    def __init__(self, faults: dict[str, list[str]] | None = None) -> None:
        self.config = default_configuration()
        self.faults = faults or {}
        self.writes: list[str] = []
        self.reads = 0
        self.saved_artifacts: list[str] = []
        self.blocked_requests: list[str] = []

    # reader
    def read_configuration(self) -> CMSConfiguration:
        self.reads += 1
        return self.config.model_copy(deep=True)

    # writer (context manager like the browser adapter)
    def __enter__(self) -> "FakeCMS":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def _fault(self, subject: str) -> str | None:
        queue = self.faults.get(subject, [])
        return queue.pop(0) if queue else None

    def _bump(self) -> None:
        self.config.config_revision += 1

    def create_custom_field(self, spec: CustomFieldSpec) -> WriteOutcome:
        self.writes.append(spec.key)
        fault = self._fault(spec.key)
        if fault in ("validation", "ui_drift", "uncertain_lost"):
            raise ConnectorError(fault.replace("_lost", ""), f"injected {fault}")
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
        self._bump()
        if fault == "uncertain_applied":
            raise ConnectorError("uncertain", "injected lost response")
        return WriteOutcome(changed=True, evidence={"channel": "fake"})

    def upsert_mapping(self, spec: MappingSpec) -> WriteOutcome:
        self.writes.append(spec.source_key)
        fault = self._fault(spec.source_key)
        if fault in ("validation", "ui_drift", "uncertain_lost"):
            raise ConnectorError(fault.replace("_lost", ""), f"injected {fault}")
        self.config.mappings = [
            m for m in self.config.mappings if m.source_key != spec.source_key
        ] + [spec]
        self._bump()
        if fault == "uncertain_applied":
            raise ConnectorError("uncertain", "injected lost response")
        return WriteOutcome(changed=True, evidence={"channel": "fake"})

    # factories for dependency overrides
    def reader_factory(self, connector: Any) -> "FakeCMS":
        return self

    def writer_factory(self, connector: Any, channel: str, artifacts_dir: Path) -> "FakeCMS":
        return self
