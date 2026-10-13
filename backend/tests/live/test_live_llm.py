"""Live mapping proposals from the real provider chain (Gemini -> Groq) for firms A, B and C.

Excluded by default because it spends API quota. Run explicitly:
    python -m pytest -m live_llm -s
Writes the raw proposals, validation results and agreement with the labelled answers to
docs/evidence/phase3-live-llm-run.json. Agreement is reported, not asserted: synthetic data is not
proof of real-world accuracy, and the evaluation suite with targets comes in Phase 6.
"""

import json
import os
from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.llm_client import LLMError, build_llm_client
from app.agent.mapping_agent import propose_mappings
from app.agent.prompts import PROMPT_VERSION
from app.config import REPO_ROOT, get_settings
from app.domain.mapping_validator import validate_proposals
from app.schemas.mapping import Decision, ProposedMapping
from tests.conftest import SOURCE_FILES
from tests.mapping_helpers import expected, source_schema, target_schema

pytestmark = pytest.mark.live_llm
EVIDENCE = REPO_ROOT / "docs" / "evidence" / "phase3-live-llm-run.json"


def _score(firm: str, proposals: list[ProposedMapping]) -> dict[str, Any]:
    truth = {m["source_key"]: m for m in expected(firm)["mappings"]}
    by_field = {p.source_field: p for p in proposals}
    rows = []
    for key, t in truth.items():
        p = by_field.get(key)
        correct = (
            p is not None
            and p.decision.value == t["decision"]
            and (t["decision"] != "map" or p.target_field == t["target_field"])
        )
        rows.append(
            {
                "source_field": key,
                "expected": t["decision"]
                + (f" -> {t['target_field']}" if "target_field" in t else ""),
                "proposed": None
                if p is None
                else p.decision.value
                + (f" -> {p.target_field}" if p.decision is Decision.MAP else ""),
                "correct": correct,
            }
        )
    mappable = [r for r, t in zip(rows, truth.values(), strict=True) if t["decision"] == "map"]
    return {
        "correct_decisions": sum(r["correct"] for r in rows),
        "total_fields": len(rows),
        "clearly_mappable_correct": sum(r["correct"] for r in mappable),
        "clearly_mappable_total": len(mappable),
        "rows": rows,
    }


def test_live_proposals_for_all_firms() -> None:
    get_settings()  # loads the git-ignored .env for keys
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
        pytest.skip("no LLM API key configured")
    llm = build_llm_client()
    target = target_schema()
    results: dict[str, Any] = {}
    for firm in SOURCE_FILES:
        source = source_schema(firm)
        try:
            proposal = propose_mappings(llm, source, target)
        except LLMError as exc:
            results[firm] = {"error": {"provider": exc.provider, "category": exc.category}}
            continue
        report = validate_proposals(
            list(proposal.args.mappings), source, target, set(proposal.untrusted_fields)
        )
        call = proposal.call
        results[firm] = {
            "provider": call.provider,
            "model": call.model,
            "fallbacks": [list(f) for f in call.fallbacks],
            "latency_ms": call.latency_ms,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "untrusted_fields": sorted(proposal.untrusted_fields),
            "validation_counts": {
                s: sum(r.status.value == s for r in report.mappings)
                for s in ("valid", "needs_review", "invalid")
            },
            "invalid": [
                {"source_field": r.source_field, "issues": [i.code for i in r.issues]}
                for r in report.mappings
                if r.status.value == "invalid"
            ],
            "coverage_gaps": [g.target_field for g in report.coverage_gaps],
            "unproposed_source_fields": report.unproposed_source_fields,
            "agreement_with_labels": _score(firm, list(proposal.args.mappings)),
            "proposals": [p.model_dump(mode="json") for p in proposal.args.mappings],
        }

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        json.dumps(
            {
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "prompt_version": PROMPT_VERSION,
                "provider_chain": llm.provider,
                "firms": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for firm, r in results.items():
        if "error" in r:
            print(f"{firm}: FAILED {r['error']}")
            continue
        s = r["agreement_with_labels"]
        print(
            f"{firm}: {r['provider']}/{r['model']} fallbacks={r['fallbacks']} "
            f"{r['latency_ms']} ms | validation {r['validation_counts']} | agreement "
            f"{s['correct_decisions']}/{s['total_fields']} (clearly mappable "
            f"{s['clearly_mappable_correct']}/{s['clearly_mappable_total']}) | "
            f"untrusted {r['untrusted_fields']}"
        )
    succeeded = [r for r in results.values() if "error" not in r]
    assert succeeded, "no provider produced proposals"
    # Structural guarantees that must hold for any provider: every field answered, parsed, checked.
    for r in succeeded:
        assert r["unproposed_source_fields"] == []
