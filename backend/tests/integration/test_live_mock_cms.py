"""End-to-end against the running mock CMS (docker compose up mock-cms; seeded demo data).

These tests talk to a real second service, so they are marked `live_cms`. They skip with a clear
reason when the mock CMS is not reachable; Phase 2 acceptance evidence requires running them.
"""

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ConnectorError
from app.integrations.mock_cms import MockCMSAdapter
from app.integrations.secrets import Credentials
from app.schemas.normalized import NormalizedSchema
from tests.conftest import FIXTURES, SOURCE_FILES, read_fixture
from tests.integration.conftest import create_project

BASE_URL = os.environ.get("MOCK_CMS_BASE_URL", "http://localhost:8100")
PASSWORD = os.environ.get("MOCK_CMS_SEED_PASSWORD", "demo-password")

pytestmark = pytest.mark.live_cms


@pytest.fixture(scope="module", autouse=True)
def require_mock_cms() -> None:
    try:
        httpx.get(f"{BASE_URL}/healthz", timeout=2).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"mock CMS not reachable at {BASE_URL}: {exc}")


def test_live_target_matches_snapshot() -> None:
    adapter = MockCMSAdapter(
        BASE_URL, Credentials("a.integration", PASSWORD), frozenset({BASE_URL})
    )
    inspection = adapter.inspect_target()
    raw = json.loads(read_fixture(FIXTURES / "target-schemas" / "mock-cms-default.json"))
    raw.pop("_comment")
    snapshot = NormalizedSchema.model_validate(raw)
    assert inspection.normalized.fields == snapshot.fields
    assert inspection.normalized.supported_transformations == snapshot.supported_transformations


def test_wrong_credentials_are_an_authorization_failure() -> None:
    adapter = MockCMSAdapter(
        BASE_URL, Credentials("a.integration", "wrong-password"), frozenset({BASE_URL})
    )
    with pytest.raises(ConnectorError) as exc:
        adapter.inspect_target()
    assert exc.value.category == "authorization"


@pytest.mark.parametrize("firm", list(SOURCE_FILES))
def test_full_phase2_flow_for_each_firm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, firm: str
) -> None:
    prefix = f"ONBOARDING_SECRET_{firm.upper().replace('-', '_')}_MOCK_CMS"
    monkeypatch.setenv(f"{prefix}_USERNAME", f"{firm[-1]}.integration")
    monkeypatch.setenv(f"{prefix}_PASSWORD", PASSWORD)
    ids = create_project(client, firm)
    url = f"/api/v1/onboarding-projects/{ids['project_id']}"

    fmt, path = SOURCE_FILES[firm]
    source = client.post(
        f"{url}/source-schema", json={"format": fmt, "content": read_fixture(path)}
    )
    assert source.status_code == 201, source.text
    target = client.post(f"{url}/inspect-target")
    assert target.status_code == 201, target.text
    assert target.json()["schema_version"]["extensions"]["tenant"] == firm

    project = client.get(url).json()
    assert project["stage"] == "ready_for_mapping"
    assert project["latest_source_schema"]["field_count"] == 10
    assert project["latest_target_schema"]["field_count"] == 10
