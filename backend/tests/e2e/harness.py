"""Shared e2e harness: real mock CMS processes on the isolated `mock_cms_e2e` database.

UI v1 on :8199 and the renamed-controls UI v2 on :8198, started once per test session and reset
before each test, so the developer's demo data is never touched. Skips when the mock CMS
virtualenv or Chromium is absent.
"""

import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT
from tests.conftest import SOURCE_FILES, read_fixture
from tests.integration.conftest import FirmContext, setup_firm
from tests.integration.test_execution_api import approve_all
from tests.mapping_helpers import FakeLLM, ground_truth_proposals

MOCK_CMS_DIR = REPO_ROOT / "mock-cms"
MOCK_PYTHON = MOCK_CMS_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
E2E_DB = os.environ.get(
    "MOCK_CMS_E2E_DATABASE_URL",
    "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/mock_cms_e2e",
)
V1, V2 = "http://127.0.0.1:8199", "http://127.0.0.1:8198"
PASSWORD = "e2e-password"


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


@pytest.fixture(scope="session")
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
