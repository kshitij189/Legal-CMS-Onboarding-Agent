"""Typed tools the LLM may call (blueprint §10).

Only *proposal* tools are registered. Tools that change a CMS (apply/execute) are intentionally not
exposed to the model at all: writes happen only through the execution service on an approved,
immutable mapping version (Phase 4+). The registry is the single allow-list.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.schemas.mapping import ProposeFieldMappingsArgs


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    # True would mean the tool changes external state; no such tool may be LLM-callable.
    writes: bool = False


PROPOSE_FIELD_MAPPINGS = ToolSpec(
    name="propose_field_mappings",
    description=(
        "Propose how each source intake field maps to the target CMS. Return exactly one entry "
        "per source field. This only records a proposal for human review; it changes nothing."
    ),
    args_model=ProposeFieldMappingsArgs,
)

LLM_TOOLS: dict[str, ToolSpec] = {PROPOSE_FIELD_MAPPINGS.name: PROPOSE_FIELD_MAPPINGS}


# Validation keywords Gemini function declarations reject (400 INVALID_ARGUMENT, verified live
# 2026-10-13). Dropping them from the *declared* schema is safe: Pydantic still enforces every
# constraint on the returned arguments.
GEMINI_UNSUPPORTED_KEYWORDS = frozenset(
    {"additionalProperties", "default", "maxLength", "minLength", "maxItems", "minItems"}
)


def inline_json_schema(
    model: type[BaseModel], drop_keywords: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """JSON Schema with $defs/$ref inlined and titles dropped (some providers reject $ref)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {
                k: resolve(v) for k, v in node.items() if k != "title" and k not in drop_keywords
            }
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved
