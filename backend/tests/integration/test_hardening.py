"""Phase 7 hardening: secrets never reach logs, errors never leak internals."""

import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.execution import get_reader_factory, get_writer_factory
from app.api.mappings import get_llm_client
from app.api.schemas import get_adapter_factory
from app.evaluation.simulation import InMemoryCMS, ScriptedLLM, target_snapshot
from app.integrations.base import TargetInspection
from app.main import app
from app.observability.logging import JsonFormatter
from tests.conftest import SOURCE_FILES, read_fixture
from tests.integration.conftest import PASSWORD, setup_firm
from tests.mapping_helpers import ground_truth_proposals


class _Collect(logging.Handler):
    """Captures every record the application emits, formatted like production logs."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(JsonFormatter().format(record))


def test_secrets_and_tokens_never_reach_the_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive a whole onboarding and assert no password, token or API key appears in any log."""
    llm_key = "gsk_FAKE_SECRET_KEY_FOR_LOG_TEST_0123456789"
    connector_password = "cms-connector-password-XYZ"
    monkeypatch.setenv("GROQ_API_KEY", llm_key)
    monkeypatch.setenv("ONBOARDING_SECRET_FIRM_A_MOCK_CMS_PASSWORD", connector_password)
    collector = _Collect()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(collector)
    root.setLevel(logging.DEBUG)
    try:
        _drive_onboarding(client, connector_password)
    finally:
        root.removeHandler(collector)
        root.setLevel(previous_level)
    logs = "\n".join(collector.lines)
    assert "POST /api/v1/auth/token" in logs, "expected request logs to be captured"
    for secret in (PASSWORD, llm_key, connector_password, "wrong-password-attempt", *_tokens_seen):
        assert secret not in logs, f"secret leaked into logs: {secret[:6]}..."


_tokens_seen: list[str] = []


def _drive_onboarding(client: TestClient, connector_password: str) -> None:

    ctx = setup_firm(client, "firm-a")
    _tokens_seen[:] = [
        h["Authorization"].removeprefix("Bearer ") for h in (ctx.specialist, ctx.admin)
    ]
    url = f"/api/v1/onboarding-projects/{ctx.project_id}"
    fmt, path = SOURCE_FILES["firm-a"]
    client.post(
        f"{url}/source-schema",
        headers=ctx.specialist,
        json={"format": fmt, "content": read_fixture(path)},
    )

    class Adapter:
        def inspect_target(self) -> TargetInspection:
            return TargetInspection(normalized=target_snapshot(), config_revision=0)

    cms = InMemoryCMS()
    app.dependency_overrides[get_adapter_factory] = lambda: lambda connector: Adapter()
    app.dependency_overrides[get_llm_client] = lambda: ScriptedLLM(ground_truth_proposals("firm-a"))
    app.dependency_overrides[get_reader_factory] = lambda: cms.reader_factory
    app.dependency_overrides[get_writer_factory] = lambda: cms.writer_factory
    client.post(f"{url}/inspect-target", headers=ctx.specialist)
    generation = client.post(f"{url}/propose-mappings", headers=ctx.specialist).json()
    for p in generation["proposals"]:
        client.post(
            f"{url}/mappings/{p['id']}/review",
            headers=ctx.specialist,
            json={"action": "approve", "comment": "ok"},
        )
    version = client.post(
        f"{url}/approve-mappings", headers=ctx.specialist, json={"generation_id": generation["id"]}
    ).json()
    client.post(
        f"{url}/execute",
        headers=ctx.specialist,
        json={"mapping_version_id": version["id"], "channel": "api"},
    )
    client.post(
        "/api/v1/auth/token",
        json={"username": "firm-a.admin", "password": "wrong-password-attempt"},
    )


def test_unexpected_errors_do_not_leak_internals() -> None:
    router = APIRouter()

    @router.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2 at /srv/app/secret.py")

    app.include_router(router)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/_boom")
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/v1/_boom"
        ]
    assert response.status_code == 500
    assert "hunter2" not in response.text and "secret.py" not in response.text
    assert "Traceback" not in response.text


def test_wrong_and_unknown_logins_are_indistinguishable(client: TestClient) -> None:
    setup_firm(client, "firm-a")
    wrong = client.post(
        "/api/v1/auth/token", json={"username": "firm-a.admin", "password": "not-it"}
    )
    unknown = client.post(
        "/api/v1/auth/token", json={"username": "nobody-here", "password": "not-it"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
