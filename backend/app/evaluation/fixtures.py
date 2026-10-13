"""Labelled fixtures shared by the evaluation suite and the tests."""

import json
from typing import Any

from app.domain.schema_normalizer import normalize_source
from app.evaluation.simulation import FIXTURES
from app.schemas.mapping import ProposedMapping
from app.schemas.normalized import NormalizedSchema, SchemaFormat

SOURCE_FILES: dict[str, tuple[str, str]] = {
    "firm-a": ("json_schema", "source-schemas/firm-a.schema.json"),
    "firm-b": ("field_export_csv", "source-schemas/firm-b.fields.csv"),
    "firm-c": ("html_form", "source-schemas/firm-c.form.html"),
}


def read(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def source_schema(firm: str) -> NormalizedSchema:
    fmt, path = SOURCE_FILES[firm]
    return normalize_source(SchemaFormat(fmt), read(path))


def expected(firm: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(read(f"expected-mappings/{firm}.json"))
    return data


def labelled_proposals(labels: dict[str, Any]) -> list[ProposedMapping]:
    """The labelled answers expressed as the LLM tool's output (what a perfect model returns)."""
    proposals = []
    for m in labels["mappings"]:
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


def ground_truth_proposals(firm: str) -> list[ProposedMapping]:
    return labelled_proposals(expected(firm))
