"""Projects, connectors and versioned schemas through the API against real PostgreSQL."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import get_adapter_factory
from app.errors import ConnectorError
from app.integrations.base import TargetInspection
from app.main import app
from app.models import CMSConnector, SchemaDefinition
from app.schemas.normalized import NormalizedSchema
from tests.conftest import FIXTURES, SOURCE_FILES, read_fixture
from tests.integration.conftest import FirmContext, setup_firm


def _upload(
    client: TestClient, ctx: FirmContext, firm: str, content: str | None = None
) -> Response:
    fmt, path = SOURCE_FILES[firm]
    response: Response = client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/source-schema",
        headers=ctx.specialist,
        json={
            "format": fmt,
            "filename": f"../../{path.name}",
            "content": content if content is not None else read_fixture(path),
        },
    )
    return response


def _target_snapshot() -> NormalizedSchema:
    raw = json.loads(read_fixture(FIXTURES / "target-schemas" / "mock-cms-default.json"))
    raw.pop("_comment")
    return NormalizedSchema.model_validate(raw)


class StubAdapter:
    def __init__(self, outcome: TargetInspection | ConnectorError) -> None:
        self.outcome = outcome
        self.calls = 0

    def inspect_target(self) -> TargetInspection:
        self.calls += 1
        if isinstance(self.outcome, ConnectorError):
            raise self.outcome
        return self.outcome


def _use_adapter(adapter: StubAdapter) -> None:
    app.dependency_overrides[get_adapter_factory] = lambda: lambda connector: adapter


def test_connector_validation(client: TestClient) -> None:
    ctx = setup_firm(client)
    base: dict[str, Any] = {
        "name": "CMS 2",
        "connector_type": "mock_cms_api",
        "credential_ref": "firm-a-cms",
    }
    evil = client.post(
        "/api/v1/connectors", headers=ctx.admin, json={**base, "base_url": "http://evil.example"}
    )
    assert evil.status_code == 502
    assert evil.json()["error"]["details"][0] == {"category": "configuration"}
    ok = client.post(
        "/api/v1/connectors", headers=ctx.admin, json={**base, "base_url": "http://localhost:8100/"}
    )
    assert ok.status_code == 201
    assert ok.json()["supported_operations"] == [
        "inspect_target",
        "read_configuration",
        "write_api",
        "write_browser",
    ]
    assert ok.json()["base_url"] == "http://localhost:8100"
    duplicate = client.post(
        "/api/v1/connectors", headers=ctx.admin, json={**base, "base_url": "http://localhost:8100"}
    )
    assert duplicate.status_code == 409
    bad_ref = client.post(
        "/api/v1/connectors",
        headers=ctx.admin,
        json={**base, "name": "x", "base_url": "http://localhost:8100", "credential_ref": "A B"},
    )
    assert bad_ref.status_code == 422


def test_project_cannot_use_another_firms_connector(client: TestClient) -> None:
    a = setup_firm(client, "firm-a")
    b = setup_firm(client, "firm-b")
    response = client.post(
        "/api/v1/onboarding-projects",
        headers=a.admin,
        json={"connector_id": b.connector_id, "name": "leak"},
    )
    assert response.status_code == 404


def test_database_enforces_same_firm_references(client: TestClient, db: Session) -> None:
    a = setup_firm(client, "firm-a")
    b = setup_firm(client, "firm-b")
    firm_a = db.execute(text("SELECT id FROM firms WHERE slug='firm-a'")).scalar_one()
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO onboarding_projects (firm_id, connector_id, name, created_at) "
                "VALUES (:f, :c, 'leak', now())"
            ),
            {"f": firm_a, "c": b.connector_id},
        )
    db.rollback()
    firm_b_admin = db.execute(text("SELECT id FROM users WHERE username='firm-b.admin'"))
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE onboarding_projects SET created_by_id = :u WHERE id = :p"),
            {"u": firm_b_admin.scalar_one(), "p": a.project_id},
        )
    db.rollback()


@pytest.mark.parametrize("firm", list(SOURCE_FILES))
def test_register_source_schema_versions_and_archives(
    client: TestClient, db: Session, artifacts_dir: Path, firm: str
) -> None:
    ctx = setup_firm(client, firm)
    first = _upload(client, ctx, firm)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    version = body["schema_version"]
    assert version["version"] == 1 and version["field_count"] == 10
    assert version["created_by"] == f"{firm}.specialist"

    # Exact uploaded bytes are archived under a server-chosen path (client filename ignored).
    stored = db.scalar(select(SchemaDefinition).where(SchemaDefinition.id == version["id"]))
    assert stored is not None and stored.artifact_path is not None
    assert ".." not in stored.artifact_path
    archived = artifacts_dir / stored.artifact_path
    assert archived.read_text(encoding="utf-8") == read_fixture(SOURCE_FILES[firm][1])
    assert stored.original_filename == f"../../{SOURCE_FILES[firm][1].name}"
    # Rows round-trip to the same normalized schema.
    assert stored.to_normalized().fingerprint() == stored.fingerprint == version["fingerprint"]

    repeat = _upload(client, ctx, firm)
    assert repeat.status_code == 200
    assert repeat.json()["created"] is False
    assert repeat.json()["schema_version"]["id"] == version["id"]


def test_changed_source_creates_new_version(client: TestClient) -> None:
    ctx = setup_firm(client)
    _upload(client, ctx, "firm-a")
    document = json.loads(read_fixture(SOURCE_FILES["firm-a"][1]))
    document["required"].append("client_email")
    second = _upload(client, ctx, "firm-a", json.dumps(document))
    assert second.status_code == 201
    assert second.json()["schema_version"]["version"] == 2
    url = f"/api/v1/onboarding-projects/{ctx.project_id}/schemas"
    versions = client.get(url, headers=ctx.specialist, params={"kind": "source"}).json()
    assert [v["version"] for v in versions] == [1, 2]
    detail = client.get(f"{url}/{versions[1]['id']}", headers=ctx.specialist).json()
    email = next(f for f in detail["fields"] if f["key"] == "client_email")
    assert email["required"] is True


def test_parse_errors_are_structured_and_store_nothing(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    response = _upload(client, ctx, "firm-b", "field_name,label\nx,X\n")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_parse_error"
    assert db.execute(text("SELECT count(*) FROM schema_definitions")).scalar_one() == 0


def test_inspect_target_and_project_stage(client: TestClient) -> None:
    ctx = setup_firm(client)
    url = f"/api/v1/onboarding-projects/{ctx.project_id}"
    project = client.get(url, headers=ctx.specialist).json()
    assert project["stage"] == "awaiting_source_schema"
    assert project["created_by"] == "firm-a.admin"
    _upload(client, ctx, "firm-a")
    assert client.get(url, headers=ctx.admin).json()["stage"] == "awaiting_target_inspection"

    adapter = StubAdapter(TargetInspection(normalized=_target_snapshot(), config_revision=0))
    _use_adapter(adapter)
    first = client.post(f"{url}/inspect-target", headers=ctx.specialist)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True and body["observed_config_revision"] == 0
    assert body["schema_version"]["target_config_revision"] == 0
    assert body["schema_version"]["supported_transformations"]["normalize_phone"] == ["phone"]
    phone = next(f for f in body["schema_version"]["fields"] if f["key"] == "contact.phone_number")
    assert phone["canonical_ref"] == "client.phone_number"

    again = client.post(f"{url}/inspect-target", headers=ctx.specialist)
    assert again.status_code == 200 and again.json()["created"] is False
    project = client.get(url, headers=ctx.specialist).json()
    assert project["stage"] == "ready_for_mapping"
    assert project["latest_target_schema"]["version"] == 1
    assert adapter.calls == 2


def test_inspect_target_connector_failure_stores_nothing(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    _use_adapter(StubAdapter(ConnectorError("authorization", "Rejected.")))
    response = client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/inspect-target", headers=ctx.specialist
    )
    assert response.status_code == 502
    assert response.json()["error"]["details"][0] == {"category": "authorization"}
    assert db.execute(text("SELECT count(*) FROM schema_definitions")).scalar_one() == 0


def test_missing_credentials_are_reported_not_guessed(client: TestClient) -> None:
    ctx = setup_firm(client, "firm-q")  # no ONBOARDING_SECRET_FIRM_Q_* variables exist
    response = client.post(
        f"/api/v1/onboarding-projects/{ctx.project_id}/inspect-target", headers=ctx.specialist
    )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["details"][0] == {"category": "configuration"}
    assert error["details"][1]["code"] == "missing_credentials"


def test_projects_and_schemas_are_invisible_to_other_firms(client: TestClient) -> None:
    a = setup_firm(client, "firm-a")
    b = setup_firm(client, "firm-b")
    schema_id = _upload(client, a, "firm-a").json()["schema_version"]["id"]
    project_url = f"/api/v1/onboarding-projects/{a.project_id}"
    # Firm B cannot read, upload to, inspect, or enumerate Firm A's project.
    assert client.get(project_url, headers=b.admin).status_code == 404
    assert client.get(f"{project_url}/schemas", headers=b.admin).status_code == 404
    assert client.get(f"{project_url}/schemas/{schema_id}", headers=b.admin).status_code == 404
    assert client.post(f"{project_url}/inspect-target", headers=b.specialist).status_code == 404
    upload = client.post(
        f"{project_url}/source-schema",
        headers=b.specialist,
        json={"format": "html_form", "content": "<input name='x'>"},
    )
    assert upload.status_code == 404
    assert [c["id"] for c in client.get("/api/v1/connectors", headers=b.admin).json()] == [
        b.connector_id
    ]
    # A schema is only reachable through the project that owns it, even within one firm.
    other = client.post(
        "/api/v1/onboarding-projects",
        headers=a.admin,
        json={"connector_id": a.connector_id, "name": "second"},
    ).json()["id"]
    url = f"/api/v1/onboarding-projects/{other}/schemas/{schema_id}"
    assert client.get(url, headers=a.admin).status_code == 404


def test_connector_secrets_are_not_stored(client: TestClient, db: Session) -> None:
    setup_firm(client)
    connector = db.scalar(select(CMSConnector))
    assert connector is not None
    columns = {c.name for c in CMSConnector.__table__.columns}
    assert not {"password", "secret", "token"} & columns
    assert connector.credential_ref == "firm-a-mock-cms"
