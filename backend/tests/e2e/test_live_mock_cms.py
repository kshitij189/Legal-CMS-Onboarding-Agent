"""Inspection against a real mock CMS process (Phase 2 acceptance).

Runs on the isolated e2e harness (its own database, reset before each test), never on the
developer's demo data — earlier versions used the demo instance on :8100 and broke whenever demo
data changed.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.errors import ConnectorError
from app.integrations.mock_cms import MockCMSAdapter
from app.integrations.secrets import Credentials
from app.schemas.normalized import NormalizedSchema
from tests.conftest import FIXTURES, SOURCE_FILES, read_fixture
from tests.e2e.harness import PASSWORD
from tests.e2e.harness import V1 as BASE_URL
from tests.integration.conftest import setup_firm

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("cms")]


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
    ctx = setup_firm(client, firm, base_url=BASE_URL)
    url = f"/api/v1/onboarding-projects/{ctx.project_id}"

    fmt, path = SOURCE_FILES[firm]
    source = client.post(
        f"{url}/source-schema",
        headers=ctx.admin,
        json={"format": fmt, "content": read_fixture(path)},
    )
    assert source.status_code == 201, source.text
    target = client.post(f"{url}/inspect-target", headers=ctx.specialist)
    assert target.status_code == 201, target.text
    assert target.json()["schema_version"]["extensions"]["tenant"] == firm
    assert target.json()["schema_version"]["created_by"] == f"{firm}.specialist"

    project = client.get(url, headers=ctx.specialist).json()
    assert project["stage"] == "ready_for_mapping"
    assert project["latest_source_schema"]["field_count"] == 10
    assert project["latest_target_schema"]["field_count"] == 10
