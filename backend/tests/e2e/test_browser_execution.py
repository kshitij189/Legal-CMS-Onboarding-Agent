"""End-to-end: Playwright drives the real mock CMS UI to apply an approved mapping version.

Starts two mock CMS processes (from mock-cms/.venv) on an isolated database `mock_cms_e2e`:
UI v1 on :8199 and the renamed-controls UI v2 on :8198. The database is reset before each test,
so the developer's demo data is never touched. Skips when the mock CMS venv or Chromium is absent.
Set WRITE_EVIDENCE=1 to record the Firm A verification report under docs/evidence/.
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, get_settings
from app.errors import ConnectorError
from app.integrations.browser_cms import BrowserCMSAdapter
from app.integrations.secrets import Credentials
from tests.conftest import SOURCE_FILES, read_fixture
from tests.integration.conftest import FirmContext, setup_firm
from tests.integration.test_execution_api import approve_all
from tests.mapping_helpers import FakeLLM, ground_truth_proposals

pytestmark = pytest.mark.e2e

MOCK_CMS_DIR = REPO_ROOT / "mock-cms"
MOCK_PYTHON = MOCK_CMS_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
E2E_DB = os.environ.get(
    "MOCK_CMS_E2E_DATABASE_URL",
    "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/mock_cms_e2e",
)
V1, V2 = "http://127.0.0.1:8199", "http://127.0.0.1:8198"
PASSWORD = "e2e-password"
EVIDENCE = REPO_ROOT / "docs" / "evidence" / "phase4-firm-a-browser-run.json"


def _cms_env(variant: str) -> dict[str, str]:
    return {
        **os.environ,
        "MOCK_CMS_DATABASE_URL": E2E_DB,
        "MOCK_CMS_UI_VARIANT": variant,
        "MOCK_CMS_SEED_PASSWORD": PASSWORD,
    }


def _reset_cms_database() -> None:
    for args in (
        ["-m", "alembic", "downgrade", "base"],
        ["-m", "alembic", "upgrade", "head"],
        ["-m", "app.seed"],
    ):
        subprocess.run(  # noqa: S603 - fixed arguments, test harness only
            [str(MOCK_PYTHON), *args],
            cwd=MOCK_CMS_DIR,
            env=_cms_env("v1"),
            check=True,
            capture_output=True,
        )


@pytest.fixture(scope="module")
def mock_cms_servers() -> Iterator[None]:
    if not MOCK_PYTHON.exists():
        pytest.skip(f"mock CMS virtualenv not found at {MOCK_PYTHON}")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - any launch problem means "no browser here"
        pytest.skip(f"Chromium not available: {exc}")
    _reset_cms_database()
    processes = [
        subprocess.Popen(  # noqa: S603
            [
                str(MOCK_PYTHON),
                "-m",
                "uvicorn",
                "app.main:app",
                "--port",
                port,
                "--log-level",
                "warning",
            ],
            cwd=MOCK_CMS_DIR,
            env=_cms_env(variant),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for port, variant in (("8199", "v1"), ("8198", "v2"))
    ]
    try:
        for base in (V1, V2):
            for _ in range(100):
                try:
                    if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.2)
            else:
                pytest.fail(f"mock CMS at {base} did not start")
        yield
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=10)


@pytest.fixture
def cms(mock_cms_servers: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_cms_database()
    for firm in ("A", "B", "C"):
        monkeypatch.setenv(
            f"ONBOARDING_SECRET_FIRM_{firm}_MOCK_CMS_USERNAME", f"{firm.lower()}.integration"
        )
        monkeypatch.setenv(f"ONBOARDING_SECRET_FIRM_{firm}_MOCK_CMS_PASSWORD", PASSWORD)


def _cms_config(base: str, firm_letter: str) -> dict[str, Any]:
    token = httpx.post(
        f"{base}/api/v1/auth/token",
        json={"username": f"{firm_letter}.integration", "password": PASSWORD},
    ).json()["access_token"]
    config: dict[str, Any] = httpx.get(
        f"{base}/api/v1/config", headers={"Authorization": f"Bearer {token}"}
    ).json()
    return config


def _approved(client: TestClient, firm: str, base_url: str) -> tuple[FirmContext, dict[str, Any]]:
    """Real target inspection through the CMS API; proposals from the labelled answers."""
    from app.api.mappings import get_llm_client
    from app.main import app

    ctx = setup_firm(client, firm, base_url=base_url)
    url = f"/api/v1/onboarding-projects/{ctx.project_id}"
    fmt, path = SOURCE_FILES[firm]
    assert (
        client.post(
            f"{url}/source-schema",
            headers=ctx.specialist,
            json={"format": fmt, "content": read_fixture(path)},
        ).status_code
        == 201
    )
    inspected = client.post(f"{url}/inspect-target", headers=ctx.specialist)
    assert inspected.status_code == 201, inspected.text
    app.dependency_overrides[get_llm_client] = lambda: FakeLLM(ground_truth_proposals(firm))
    generation = client.post(f"{url}/propose-mappings", headers=ctx.specialist).json()
    return ctx, approve_all(client, ctx, generation)


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
