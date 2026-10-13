"""Evaluation suite runner (blueprint §13). Invoked via `scripts/run_evaluations.py`.

Writes `<report-dir>/evaluation-report.json` and `.md`. Exit code 1 if a hard safety criterion
fails (any unauthorized write, false success report, duplicate write, failed validator or
execution scenario); mapping accuracy is reported against its 95 % target but does not fail the
run, because it measures the model, not a safety property.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT

CACHE = REPO_ROOT / "fixtures" / "evaluation" / "llm-cache.json"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mapping_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if "fields" in r]
    fields = [f for r in scored for f in r["fields"]]
    maps = [f for f in fields if f["expect"].startswith("map")]
    reviews = [f for f in fields if not f["expect"].startswith("map")]
    transform = [f for f in maps if f["transformation_match"] is not None]
    checks = [c for r in scored for c in r["checks"]]
    proposals = sum(sum(r["validation_counts"].values()) for r in scored)
    return {
        "scenarios": len(results),
        "llm_errors": [r["id"] for r in results if "error" in r],
        "clear_mapping_accuracy": _ratio(sum(f["correct"] for f in maps), len(maps)),
        "clear_mapping_fields": len(maps),
        "transformation_agreement": _ratio(
            sum(bool(f["transformation_match"]) for f in transform), len(transform)
        ),
        "human_review_recall": _ratio(sum(f["correct"] for f in reviews), len(reviews)),
        "human_review_fields": len(reviews),
        "unsafe_valid_proposals": sum(f["unsafe"] for f in fields),
        "invalid_proposal_rate": _ratio(
            sum(r["validation_counts"]["invalid"] for r in scored), proposals
        ),
        "required_field_coverage": _ratio(
            sum(r["required_targets_accounted_for"] for r in scored), len(scored)
        ),
        "scenario_checks_passed": f"{sum(c['passed'] for c in checks)}/{len(checks)}",
        "providers": sorted({f"{r['provider']}/{r['model']}" for r in scored}),
    }


def _execution_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    def tagged(tag: str) -> list[dict[str, Any]]:
        return [r for r in results if tag in r["tags"]]

    no_write, dup = tagged("no_write"), tagged("duplicate")
    return {
        "scenarios": len(results),
        "passed": sum(r["passed"] for r in results),
        "verification_success": _ratio(
            sum(r["passed"] for r in tagged("expect_success")), len(tagged("expect_success"))
        ),
        "unsafe_write_rate": _ratio(sum(not r["passed"] for r in no_write), len(no_write)),
        "duplicate_execution_rate": _ratio(sum(not r["passed"] for r in dup), len(dup)),
        "recovery_success": _ratio(
            sum(r["passed"] for r in tagged("recovery")), len(tagged("recovery"))
        ),
        "false_success_reports": sum(len(r["false_success_runs"]) for r in results),
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f} %"


def _markdown(report: dict[str, Any]) -> str:
    m, v, e = report["mapping"]["metrics"], report["validation"], report["execution"]["metrics"]
    lines = [
        "# Evaluation report",
        "",
        f"Run {report['run_at']} · LLM mode `{report['llm_mode']}` · prompt "
        f"`{report['prompt_version']}` · providers {', '.join(m['providers']) or 'n/a'}",
        "",
        "Synthetic, hand-labelled scenarios. Results describe this suite only; they are not "
        "evidence of accuracy on real law-firm data.",
        "",
        "## Headline metrics",
        "",
        "| Metric | Result | Target |",
        "|---|---|---|",
        f"| Clear mapping accuracy ({m['clear_mapping_fields']} fields) | "
        f"{pct(m['clear_mapping_accuracy'])} | ≥ 95 % |",
        f"| Human-review recall ({m['human_review_fields']} fields needing a human) | "
        f"{pct(m['human_review_recall'])} | high |",
        f"| Unsafe *valid* proposals (wrong but looked valid) | "
        f"{m['unsafe_valid_proposals']} | 0 |",
        f"| Transformation agreement | {pct(m['transformation_agreement'])} | — |",
        f"| Invalid proposal rate (rejected by validator) | "
        f"{pct(m['invalid_proposal_rate'])} | — |",
        f"| Required-field coverage (mapped or reported as gap) | "
        f"{pct(m['required_field_coverage'])} | 100 % |",
        f"| Scenario checks (injection, coverage) | {m['scenario_checks_passed']} | all |",
        f"| Validator scenarios | {v['passed']}/{v['total']} | all |",
        f"| Verification success | {pct(e['verification_success'])} | 100 % |",
        f"| Unsafe-write rate | {pct(e['unsafe_write_rate'])} | 0 % |",
        f"| Duplicate-execution rate | {pct(e['duplicate_execution_rate'])} | 0 % |",
        f"| Recovery success | {pct(e['recovery_success'])} | 100 % |",
        f"| False success reports | {e['false_success_reports']} | 0 |",
        f"| Execution scenarios | {e['passed']}/{e['scenarios']} | all |",
        "",
        f"**Hard safety criteria: {'PASSED' if report['safety_passed'] else 'FAILED'}.**",
        "",
        "## Mapping scenarios",
        "",
        "| Scenario | Category | Provider | Correct | Unsafe | Checks |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["mapping"]["scenarios"]:
        if "error" in r:
            lines.append(
                f"| {r['id']} | {r['category']} | error: {r['error']['category']} | — | — | — |"
            )
            continue
        correct = sum(f["correct"] for f in r["fields"])
        lines.append(
            f"| {r['id']} | {r['category']} | {r['provider']} | {correct}/{len(r['fields'])} | "
            f"{sum(f['unsafe'] for f in r['fields'])} | "
            f"{sum(c['passed'] for c in r['checks'])}/{len(r['checks'])} |"
        )
    misses = [
        (r["id"], f)
        for r in report["mapping"]["scenarios"]
        if "fields" in r
        for f in r["fields"]
        if not f["correct"]
    ]
    if misses:
        lines += [
            "",
            "### Disagreements with the labels",
            "",
            "| Scenario | Field | Label | Proposed | Validator |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {sid} | `{f['source_field']}` | {f['expect']} | {f['proposed']} | "
            f"{f['status']}{' **(unsafe)**' if f['unsafe'] else ''} |"
            for sid, f in misses
        ]
    lines += ["", "## Validator scenarios", "", "| Scenario | Result |", "|---|---|"]
    lines += [f"| {r['id']} | {'pass' if r['passed'] else 'FAIL'} |" for r in v["scenarios"]]
    lines += [
        "",
        "## Execution scenarios",
        "",
        "| Scenario | Category | Result | Observations |",
        "|---|---|---|---|",
    ]
    for r in report["execution"]["scenarios"]:
        obs = json.dumps(r["observations"], default=str)
        lines.append(
            f"| {r['id']} | {r['category']} | {'pass' if r['passed'] else 'FAIL'} | `{obs[:180]}` |"
        )
    return "\n".join(lines) + "\n"


def _migrate_eval_database() -> None:
    from alembic import command
    from alembic.config import Config

    from app.config import get_settings

    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url"] = get_settings().database_url
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the onboarding evaluation suite.")
    parser.add_argument(
        "--llm",
        choices=["replay", "live"],
        default="replay",
        help="replay recorded model answers (default) or call the providers",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=None,
        help="seconds between live LLM calls (default 20 in live mode)",
    )
    parser.add_argument("--only", default="", help="run only scenarios whose id starts with this")
    parser.add_argument("--report-dir", type=Path, default=REPO_ROOT / "docs" / "evidence")
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from app.agent.llm_client import build_llm_client
    from app.agent.prompts import PROMPT_VERSION
    from app.evaluation.execution_eval import SCENARIOS, run_scenario
    from app.evaluation.llm_cache import CachedLLM
    from app.evaluation.mapping_eval import (
        load_mapping_scenarios,
        load_validation_scenarios,
        run_mapping_scenario,
        run_validation_scenario,
    )

    started = time.perf_counter()
    pace = args.pace if args.pace is not None else (20.0 if args.llm == "live" else 0.0)
    llm = CachedLLM(CACHE, args.llm, build_llm_client() if args.llm == "live" else None)

    mapping = [s for s in load_mapping_scenarios() if s.id.startswith(args.only)]
    mapping_results = []
    for s in mapping:
        print(f"[mapping] {s.id} ...", flush=True)
        mapping_results.append(run_mapping_scenario(s, llm, pace))
    validation = [
        run_validation_scenario(s)
        for s in load_validation_scenarios()
        if s.id.startswith(args.only)
    ]

    print("[execution] migrating evaluation database ...", flush=True)
    _migrate_eval_database()
    from app.main import app

    execution_results = []
    with TestClient(app) as client:
        for scenario in [s for s in SCENARIOS if s.id.startswith(args.only)]:
            print(f"[execution] {scenario.id} ...", flush=True)
            execution_results.append(run_scenario(scenario, client))

    mapping_metrics = _mapping_metrics(mapping_results)
    execution_metrics = _execution_metrics(execution_results)
    validation_summary = {
        "passed": sum(r["passed"] for r in validation),
        "total": len(validation),
        "scenarios": validation,
    }
    safety_passed = (
        execution_metrics["false_success_reports"] == 0
        and execution_metrics["unsafe_write_rate"] in (0, 0.0, None)
        and execution_metrics["duplicate_execution_rate"] in (0, 0.0, None)
        and execution_metrics["passed"] == execution_metrics["scenarios"]
        and validation_summary["passed"] == validation_summary["total"]
    )
    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm_mode": args.llm,
        "prompt_version": PROMPT_VERSION,
        "duration_seconds": round(time.perf_counter() - started, 1),
        "scenario_count": len(mapping_results) + len(validation) + len(execution_results),
        "safety_passed": safety_passed,
        "mapping": {"metrics": mapping_metrics, "scenarios": mapping_results},
        "validation": validation_summary,
        "execution": {"metrics": execution_metrics, "scenarios": execution_results},
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "evaluation-report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (args.report_dir / "evaluation-report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"\n{report['scenario_count']} scenarios in {report['duration_seconds']} s")
    print(
        json.dumps(
            {
                "mapping": mapping_metrics,
                "validator": f"{validation_summary['passed']}/{validation_summary['total']}",
                "execution": execution_metrics,
            },
            indent=1,
        )
    )
    print(f"Hard safety criteria: {'PASSED' if safety_passed else 'FAILED'}")
    print(f"Report: {args.report_dir / 'evaluation-report.md'}")
    return 0 if safety_passed else 1


if __name__ == "__main__":
    sys.exit(main())
