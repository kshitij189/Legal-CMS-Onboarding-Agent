"""Repeatable end-to-end demonstration against the running Compose stack (Phase 7).

    docker compose up -d --build
    backend\\.venv\\Scripts\\python scripts\\demo.py --reset

`--reset` rebuilds the demo databases (mock CMS + onboarding) from migrations and seeds them, so
every run starts from the same state. It deletes all demo data — synthetic accounts only.
The LLM provider configured for the backend writes the proposals (a few requests).

Scenes (blueprint §18 definition of done):
  1. Normal onboarding (Firm A): upload, inspect, propose, review, approve, execute in the CMS UI
     with Playwright, read back and verify.
  2. Re-run the same approved version: nothing is written twice.
  3. Deliberate failure + safe recovery (Firm C): the CMS UI changes, the run stops before
     guessing with nothing written; the UI is restored and the same version completes. The
     ambiguous field and the prompt-injection attempt in Firm C's form are routed to review.
  4. Authorization: a user of another firm and a role without permission are refused.
  5. Schema change after approval: execution of the old version is refused.
  6. Worker crash (Firm B): the worker container is killed mid-run and restarted; the run
     completes with every step written exactly once.
  7. Duplicate request: the same Idempotency-Key returns the same run.
Writes a narrated transcript to docs/evidence/phase7-demo.md.
"""

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
API = os.environ.get("DEMO_API", "http://localhost:8200/api/v1")
CMS = os.environ.get("DEMO_CMS", "http://localhost:8100")
PASSWORD = "demo-password"
FIXTURES = REPO / "fixtures" / "source-schemas"
TRANSCRIPT: list[str] = []
http = httpx.Client(timeout=300)


def say(text: str = "") -> None:
    print(text, flush=True)
    TRANSCRIPT.append(text)


def compose(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO, check=True, capture_output=True,
                   env={**os.environ, **(env or {})})


def wait_healthy(url: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if http.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become healthy")


def set_cms_ui(variant: str) -> None:
    compose("up", "-d", "mock-cms", env={"MOCK_CMS_UI_VARIANT": variant})
    wait_healthy(f"{CMS}/healthz")


def reset() -> None:
    for service, seed in (("mock-cms", "app.seed"), ("backend", "app.seed")):
        compose("exec", "-T", service, "alembic", "downgrade", "base")
        compose("exec", "-T", service, "alembic", "upgrade", "head")
        compose("exec", "-T", service, "python", "-m", seed)
    compose("restart", "worker")


def login(username: str) -> dict[str, str]:
    r = http.post(f"{API}/auth/token", json={"username": username, "password": PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def cms_config(letter: str) -> dict[str, Any]:
    token = http.post(f"{CMS}/api/v1/auth/token", json={
        "username": f"{letter}.integration", "password": PASSWORD}).json()["access_token"]
    config: dict[str, Any] = http.get(
        f"{CMS}/api/v1/config", headers={"Authorization": f"Bearer {token}"}).json()
    return config


def call(method: str, path: str, headers: dict[str, str], **kwargs: Any) -> httpx.Response:
    return http.request(method, f"{API}{path}", headers=headers, **kwargs)


def approved_project(
    letter: str, slug: str, fmt: str, fixture: str
) -> tuple[str, dict[str, str], dict[str, Any], dict[str, Any]]:
    """Admin creates the project; the specialist uploads, inspects, proposes, reviews, approves."""
    admin, spec = login(f"{letter}.admin"), login(f"{letter}.specialist")
    connector = call("GET", "/connectors", admin).json()[0]
    project = call("POST", "/onboarding-projects", admin,
                   json={"connector_id": connector["id"], "name": f"{slug} demo"}).json()
    url = f"/onboarding-projects/{project['id']}"
    source = call("POST", f"{url}/source-schema", spec, json={
        "format": fmt, "content": (FIXTURES / fixture).read_text(encoding="utf-8")}).json()
    say(f"- Source schema v{source['schema_version']['version']} uploaded "
        f"({source['schema_version']['field_count']} fields, {fmt}).")
    target = call("POST", f"{url}/inspect-target", spec).json()
    say(f"- Target CMS inspected: {target['schema_version']['field_count']} fields, "
        f"CMS revision {target['observed_config_revision']}.")
    gen = call("POST", f"{url}/propose-mappings", spec)
    gen.raise_for_status()
    generation = gen.json()
    statuses = {s: sum(p["validation_status"] == s for p in generation["proposals"])
                for s in ("valid", "needs_review", "invalid")}
    say(f"- {len(generation['proposals'])} proposals from {generation['provider']}/"
        f"{generation['model']}; deterministic checks: {statuses}.")
    if generation["untrusted_source_fields"]:
        say(f"- Instruction-like text detected in {generation['untrusted_source_fields']}: "
            "treated as data, forced to human review.")
    for p in generation["proposals"]:
        ambiguous = p["decision"] == "ambiguous"
        reject = ambiguous or p["validation_status"] == "invalid"
        comment = None
        if p["validation_status"] == "needs_review" and not reject:
            comment = "Checked against the firm's form."
        call("POST", f"{url}/mappings/{p['id']}/review", spec,
             json={"action": "reject" if reject else "approve", "comment": comment}
             ).raise_for_status()
        if reject:
            why = "ambiguous — a person must decide" if ambiguous else "invalid"
            say(f"  - `{p['source_field']}` rejected by the reviewer ({why}).")
    version = call("POST", f"{url}/approve-mappings", spec,
                   json={"generation_id": generation["id"]}).json()
    say(f"- Approved as immutable mapping version {version['version']} "
        f"({len(version['items'])} approved mapping items, fingerprint {version['fingerprint'][:12]}).")
    return url, spec, version, project


def execute(url: str, headers: dict[str, str], version_id: int, key: str | None = None
            ) -> httpx.Response:
    extra = {"Idempotency-Key": key} if key else {}
    return call("POST", f"{url}/execute", {**headers, **extra},
                json={"mapping_version_id": version_id, "channel": "browser"})


def wait_run(url: str, headers: dict[str, str], run_id: int, timeout: float = 300
             ) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run: dict[str, Any] = call("GET", f"{url}/runs/{run_id}", headers).json()
        if run["status"] != "running":
            return run
        time.sleep(1)
    raise RuntimeError(f"run {run_id} did not finish")


def describe(run: dict[str, Any]) -> str:
    return (f"run {run['id']} **{run['status']}** — steps {run['summary']['steps']}, "
            f"verification {run['summary']['verification']}, CMS revision "
            f"{run['config_revision_before']} → {run['config_revision_after']}"
            + (f", reason `{run['error_category']}`: {run['error_message']}"
               if run["error_category"] else ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="rebuild and reseed demo databases")
    args = parser.parse_args()
    started = time.monotonic()
    say("# Demonstration transcript")
    say()
    say(f"Run {datetime.now(UTC).isoformat(timespec='seconds')} against the Compose stack "
        "(backend, Temporal worker, mock CMS). Synthetic data only.")
    wait_healthy(API.removesuffix("/api/v1") + "/healthz")
    set_cms_ui("v1")
    if args.reset:
        reset()
        say("Demo databases rebuilt from migrations and reseeded.")
    results: list[tuple[str, bool]] = []

    say("\n## 1. Normal onboarding — Firm A (browser channel)\n")
    url_a, spec_a, version_a, _ = approved_project("a", "Alder", "json_schema",
                                                   "firm-a.schema.json")
    started_run = execute(url_a, spec_a, version_a["id"])
    say(f"- Execute → HTTP {started_run.status_code} (accepted; runs on the Temporal worker).")
    run = wait_run(url_a, spec_a, started_run.json()["id"])
    independent = cms_config("a")
    say(f"- {describe(run)}")
    say(f"- Independent read of the CMS: revision {independent['config_revision']}, "
        f"{len(independent['intake_mappings'])} intake mappings stored.")
    results.append(("Normal onboarding verified", run["status"] == "succeeded"))

    say("\n## 2. Re-run the same approved version\n")
    rerun = wait_run(url_a, spec_a, execute(url_a, spec_a, version_a["id"]).json()["id"])
    say(f"- {describe(rerun)}")
    results.append(("Re-run wrote nothing new",
                    rerun["status"] == "succeeded" and set(rerun["summary"]["steps"])
                    == {"already_applied"}
                    and cms_config("a")["config_revision"] == independent["config_revision"]))

    say("\n## 3. Deliberate failure and safe recovery — Firm C, CMS UI changes\n")
    url_c, spec_c, version_c, _ = approved_project("c", "Cedar", "html_form", "firm-c.form.html")
    set_cms_ui("v2")
    say("- The CMS is switched to a new UI release (renamed form controls).")
    failed = wait_run(url_c, spec_c, execute(url_c, spec_c, version_c["id"]).json()["id"])
    say(f"- {describe(failed)}")
    before_fix = cms_config("c")["config_revision"]
    say(f"- CMS revision after the failed run: {before_fix} (nothing was written).")
    set_cms_ui("v1")
    say("- The CMS UI is restored; the same approved version is executed again.")
    recovered = wait_run(url_c, spec_c, execute(url_c, spec_c, version_c["id"]).json()["id"])
    say(f"- {describe(recovered)}")
    results.append(("UI change stopped safely with zero writes",
                    failed["status"] == "failed" and failed["error_category"] == "ui_drift"
                    and before_fix == 0))
    results.append(("Same version recovered after the fix", recovered["status"] == "succeeded"))

    say("\n## 4. Authorization failures\n")
    spec_b = login("b.specialist")
    other_firm = call("GET", url_a, spec_b)
    say(f"- Firm B specialist opens Firm A's project → HTTP {other_firm.status_code} "
        "(another firm's data is indistinguishable from missing).")
    no_role = call("POST", "/onboarding-projects", spec_b, json={"connector_id": 1, "name": "x"})
    say(f"- Integration specialist tries to create a project → HTTP {no_role.status_code} "
        f"`{no_role.json()['error']['details'][0]['code']}`.")
    results.append(("Authorization enforced",
                    other_firm.status_code == 404 and no_role.status_code == 403))

    say("\n## 5. Schema change after approval\n")
    changed = (FIXTURES / "firm-a.schema.json").read_text(encoding="utf-8").replace(
        '"required": ["client_full_name"', '"required": ["client_email", "client_full_name"')
    call("POST", f"{url_a}/source-schema", spec_a,
         json={"format": "json_schema", "content": changed}).raise_for_status()
    refused = execute(url_a, spec_a, version_a["id"])
    say(f"- Firm A changes its form (email now required); executing the old approval → HTTP "
        f"{refused.status_code} `{refused.json()['error']['details'][0]['code']}`.")
    results.append(("Stale approval refused", refused.status_code == 409))

    say("\n## 6. Worker crash during a run — Firm B\n")
    url_b, spec_b2, version_b, _ = approved_project("b", "Birch", "field_export_csv",
                                                    "firm-b.fields.csv")
    run_id = execute(url_b, spec_b2, version_b["id"]).json()["id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        live = call("GET", f"{url_b}/runs/{run_id}", spec_b2).json()
        done = sum(s["status"] in ("applied", "already_applied") for s in live["steps"])
        if done >= 2 or live["status"] != "running":
            break
        time.sleep(0.3)
    compose("kill", "worker")
    say(f"- Worker container killed after {done} of {len(live['steps'])} steps.")
    time.sleep(3)
    paused = call("GET", f"{url_b}/runs/{run_id}", spec_b2).json()
    say(f"- While the worker is down: run `{paused['status']}`, steps {paused['summary']['steps']}.")
    compose("start", "worker")
    say("- Worker restarted; Temporal hands the run back.")
    crash = wait_run(url_b, spec_b2, run_id)
    attempts = sorted({s["attempts"] for s in crash["steps"]})
    say(f"- {describe(crash)}; attempts per step: {attempts}.")
    results.append(("Run survived a worker crash without repeats",
                    crash["status"] == "succeeded" and attempts == [1]))

    say("\n## 7. Duplicate request\n")
    first = execute(url_b, spec_b2, version_b["id"], key="demo-retry-1")
    second = execute(url_b, spec_b2, version_b["id"], key="demo-retry-1")
    wait_run(url_b, spec_b2, first.json()["id"])
    say(f"- Same Idempotency-Key twice → HTTP {first.status_code} then {second.status_code}, "
        f"run ids {first.json()['id']} and {second.json()['id']}.")
    results.append(("Duplicate request returned the same run",
                    first.json()["id"] == second.json()["id"]))

    say("\n## Summary\n")
    say("| Check | Result |")
    say("|---|---|")
    for name, ok in results:
        say(f"| {name} | {'PASS' if ok else 'FAIL'} |")
    say(f"\nCompleted in {time.monotonic() - started:.0f} s.")
    out = REPO / "docs" / "evidence" / "phase7-demo.md"
    out.write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    print(f"\nTranscript: {out}")
    if not all(ok for _, ok in results):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            set_cms_ui("v1")  # never leave the demo CMS on the changed UI
        except Exception as exc:  # noqa: BLE001 - best effort clean-up
            print(f"warning: could not restore mock CMS UI: {exc}")
        print(json.dumps({"api": API, "cms": CMS}))
