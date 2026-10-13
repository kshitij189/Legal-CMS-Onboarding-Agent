"""End-to-end: Playwright drives the real mock CMS UI to apply an approved mapping version.

Starts two mock CMS processes (from mock-cms/.venv) on an isolated database `mock_cms_e2e`:
UI v1 on :8199 and the renamed-controls UI v2 on :8198. The database is reset before each test,
so the developer's demo data is never touched. Skips when the mock CMS venv or Chromium is absent.
Set WRITE_EVIDENCE=1 to record the Firm A verification report under docs/evidence/.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, get_settings
from app.errors import ConnectorError
from app.integrations.browser_cms import BrowserCMSAdapter
from app.integrations.secrets import Credentials
from tests.e2e.harness import PASSWORD, V1, V2, _approved, _cms_config
from tests.integration.conftest import FirmContext

pytestmark = pytest.mark.e2e

EVIDENCE = REPO_ROOT / "docs" / "evidence" / "phase4-firm-a-browser-run.json"


def _execute(client: TestClient, ctx: FirmContext, version_id: int) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/execute",
        headers=ctx.specialist,
        json={"mapping_version_id": version_id, "channel": "browser"},
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_browser_applies_firm_a_and_read_back_verifies(cms: None, client: TestClient) -> None:
    ctx, version = _approved(client, "firm-a", V1)
    run = _execute(client, ctx, version["id"])
    assert run["status"] == "succeeded", json.dumps(run, indent=1)[:3000]
    assert run["summary"] == {"steps": {"applied": 8}, "verification": {"match": 8}}
    assert all(s["evidence"]["notice"].startswith("Saved.") for s in run["steps"])
    assert run["artifact_dir"] is None  # no browser artifacts kept for a clean run

    # Independent check straight against the CMS API.
    stored = {m["source_key"]: m for m in _cms_config(V1, "a")["intake_mappings"]}
    assert stored["client_mobile"]["transformation"] == "normalize_phone"
    assert stored["matter_type"]["value_map"] == {
        "Family": "family_law",
        "Personal Injury": "personal_injury",
        "Employment": "employment",
        "Immigration": "immigration",
    }
    if os.environ.get("WRITE_EVIDENCE") == "1":
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(run, indent=2, default=str) + "\n", encoding="utf-8")

    # Re-running the same approved version changes nothing in the CMS.
    revision = _cms_config(V1, "a")["config_revision"]
    rerun = _execute(client, ctx, version["id"])
    assert rerun["summary"]["steps"] == {"already_applied": 8}
    assert _cms_config(V1, "a")["config_revision"] == revision


def test_browser_creates_custom_field_before_mapping_to_it(cms: None, client: TestClient) -> None:
    ctx, version = _approved(client, "firm-c", V1)
    run = _execute(client, ctx, version["id"])
    assert run["status"] == "succeeded", json.dumps(run, indent=1)[:3000]
    assert run["steps"][0]["subject"] == "matter.custom.review_status"
    field = next(
        f for f in _cms_config(V1, "c")["fields"] if f["key"] == "matter.custom.review_status"
    )
    assert (field["data_type"], field["required"], field["options"]) == (
        "enum",
        True,
        ["pending", "reviewed", "escalated"],
    )


def test_renamed_controls_stop_the_run_safely(cms: None, client: TestClient) -> None:
    ctx, version = _approved(client, "firm-a", V2)
    before = _cms_config(V2, "a")
    run = _execute(client, ctx, version["id"])
    assert run["status"] == "failed" and run["error_category"] == "ui_drift"
    failed = run["steps"][0]
    assert failed["status"] == "failed"
    detail = failed["evidence"]["details"][0]
    assert (detail["code"], detail["control"]) == ("control_missing", "Source field key")
    assert {s["status"] for s in run["steps"][1:]} == {"skipped"}
    # Nothing was written, and the failure left a trace and screenshot for diagnosis.
    after = _cms_config(V2, "a")
    assert after["config_revision"] == before["config_revision"] == 0
    assert after["intake_mappings"] == []
    run_dir = get_settings().artifacts_dir / run["artifact_dir"]
    assert sorted(p.name for p in run_dir.iterdir()) == ["failure.png", "trace.zip"]
    assert {c["status"] for c in run["verification"]} == {"missing"}


def test_wrong_credentials_fail_as_authorization(
    cms: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, version = _approved(client, "firm-a", V1)
    monkeypatch.setenv("ONBOARDING_SECRET_FIRM_A_MOCK_CMS_PASSWORD", "wrong")
    run = client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/execute",
        headers=ctx.specialist,
        json={"mapping_version_id": version["id"], "channel": "browser"},
    ).json()
    # The API reader is refused first: no browser session, no writes.
    assert run["status"] == "failed" and run["error_category"] == "authorization"
    assert _cms_config(V1, "a")["intake_mappings"] == []


def test_navigation_outside_allowed_origins_is_blocked(cms: None, tmp_path: Path) -> None:
    from playwright.sync_api import Error as PlaywrightError

    adapter = BrowserCMSAdapter(
        V1, Credentials("a.integration", PASSWORD), frozenset({V1}), tmp_path, timeout_ms=3000
    )
    with adapter, pytest.raises(PlaywrightError):
        adapter.page.goto("https://example.com/")
    assert adapter.blocked_requests == ["https://example.com/"]


class _Redirector(BaseHTTPRequestHandler):
    """Allowed origin that redirects off-site: redirects bypass request routing."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "https://example.com/")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_redirect_off_the_allowed_origins_stops_safely(cms: None, tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    redirector = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        adapter = BrowserCMSAdapter(
            V1,
            Credentials("a.integration", PASSWORD),
            frozenset({V1, redirector}),
            tmp_path,
            timeout_ms=3000,
        )
        with pytest.raises(ConnectorError) as exc, adapter:
            adapter._goto(f"{redirector}/start")
    finally:
        server.shutdown()
    # The redirect is refused before the browser follows it: nothing is sent off-site.
    assert exc.value.category == "security"
    assert exc.value.details[0]["code"] == "navigation_blocked"
    assert exc.value.details[0]["blocked"] == "https://example.com/"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["failure.png", "trace.zip"]


def test_disallowed_base_url_is_rejected_before_launching(tmp_path: Path) -> None:
    with pytest.raises(ConnectorError) as exc:
        BrowserCMSAdapter("http://evil.example", Credentials("u", "p"), frozenset({V1}), tmp_path)
    assert exc.value.category == "configuration"
    assert sys.platform  # keeps the import used on every platform
