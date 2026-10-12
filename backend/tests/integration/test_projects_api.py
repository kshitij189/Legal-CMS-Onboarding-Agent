"""Projects, connectors and versioned schemas through the API against real PostgreSQL."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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
from tests.integration.conftest import create_project


def _upload(client: TestClient, project_id: int, firm: str, content: str | None = None) -> object:
    fmt, path = SOURCE_FILES[firm]
    return client.post(
        f"/api/v1/onboarding-projects/{project_id}/source-schema",
        json={
            "format": fmt,
            "filename": f"../../{path.name}",
            "content": content if content is not None else read_fixture(path),
        },
    )


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


def test_firm_and_connector_validation(client: TestClient) -> None:
    firm = client.post("/api/v1/firms", json={"slug": "firm-a", "name": "Firm A"})
    assert firm.status_code == 201
    duplicate = client.post("/api/v1/firms", json={"slug": "firm-a", "name": "Again"})
    assert duplicate.status_code == 409
    assert client.post("/api/v1/firms", json={"slug": "Bad Slug", "name": "x"}).status_code == 422

    firm_id = firm.json()["id"]
    base = {"name": "CMS", "connector_type": "mock_cms_api", "credential_ref": "firm-a-cms"}
    evil = client.post(
        f"/api/v1/firms/{firm_id}/connectors", json={**base, "base_url": "http://evil.example"}
    )
    assert evil.status_code == 502
    assert evil.json()["error"]["details"][0] == {"category": "configuration"}
    ok = client.post(
        f"/api/v1/firms/{firm_id}/connectors", json={**base, "base_url": "http://localhost:8100/"}
    )
    assert ok.status_code == 201
    assert ok.json()["supported_operations"] == ["inspect_target"]
    assert ok.json()["base_url"] == "http://localhost:8100"
    bad_ref = client.post(
        f"/api/v1/firms/{firm_id}/connectors",
        json={**base, "name": "x", "base_url": "http://localhost:8100", "credential_ref": "A B"},
    )
    assert bad_ref.status_code == 422
    assert (
        client.post(
            "/api/v1/firms/999/connectors", json={**base, "base_url": "http://localhost:8100"}
        ).status_code
        == 404
    )


def test_project_cannot_use_another_firms_connector(client: TestClient) -> None:
    a = create_project(client, "firm-a")
    b = create_project(client, "firm-b")
    response = client.post(
        "/api/v1/onboarding-projects",
        json={"firm_id": a["firm_id"], "connector_id": b["connector_id"], "name": "leak"},
    )
    assert response.status_code == 404


def test_database_enforces_same_firm_references(client: TestClient, db: Session) -> None:
    a = create_project(client, "firm-a")
    b = create_project(client, "firm-b")
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO onboarding_projects (firm_id, connector_id, name, created_at) "
                "VALUES (:f, :c, 'leak', now())"
            ),
            {"f": a["firm_id"], "c": b["connector_id"]},
        )
    db.rollback()


@pytest.mark.parametrize("firm", list(SOURCE_FILES))
def test_register_source_schema_versions_and_archives(
    client: TestClient, db: Session, artifacts_dir: Path, firm: str
) -> None:
    ids = create_project(client, firm)
    first = _upload(client, ids["project_id"], firm)
    assert first.status_code == 201, first.text  # type: ignore[attr-defined]
    body = first.json()  # type: ignore[attr-defined]
    assert body["created"] is True
    version = body["schema_version"]
    assert version["version"] == 1 and version["field_count"] == 10

    # Exact uploaded bytes are archived under a server-chosen path (client filename ignored).
    stored = db.scalar(select(SchemaDefinition).where(SchemaDefinition.id == version["id"]))
    assert stored is not None and stored.artifact_path is not None
    assert ".." not in stored.artifact_path
    archived = artifacts_dir / stored.artifact_path
    assert archived.read_text(encoding="utf-8") == read_fixture(SOURCE_FILES[firm][1])
    assert stored.original_filename == f"../../{SOURCE_FILES[firm][1].name}"
    # Rows round-trip to the same normalized schema.
    assert stored.to_normalized().fingerprint() == stored.fingerprint == version["fingerprint"]

    repeat = _upload(client, ids["project_id"], firm)
    assert repeat.status_code == 200  # type: ignore[attr-defined]
    assert repeat.json()["created"] is False  # type: ignore[attr-defined]
    assert repeat.json()["schema_version"]["id"] == version["id"]  # type: ignore[attr-defined]


def test_changed_source_creates_new_version(client: TestClient) -> None:
    ids = create_project(client)
    _upload(client, ids["project_id"], "firm-a")
    document = json.loads(read_fixture(SOURCE_FILES["firm-a"][1]))
    document["required"].append("client_email")
    second = _upload(client, ids["project_id"], "firm-a", json.dumps(document))
    assert second.status_code == 201  # type: ignore[attr-defined]
    assert second.json()["schema_version"]["version"] == 2  # type: ignore[attr-defined]
    versions = client.get(
        f"/api/v1/onboarding-projects/{ids['project_id']}/schemas", params={"kind": "source"}
    ).json()
    assert [v["version"] for v in versions] == [1, 2]
    detail = client.get(
        f"/api/v1/onboarding-projects/{ids['project_id']}/schemas/{versions[1]['id']}"
    ).json()
    email = next(f for f in detail["fields"] if f["key"] == "client_email")
    assert email["required"] is True


def test_parse_errors_are_structured_and_store_nothing(client: TestClient, db: Session) -> None:
    ids = create_project(client)
    response = _upload(client, ids["project_id"], "firm-b", "field_name,label\nx,X\n")
    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json()["error"]["code"] == "schema_parse_error"  # type: ignore[attr-defined]
    assert db.execute(text("SELECT count(*) FROM schema_definitions")).scalar_one() == 0


def test_inspect_target_and_project_stage(client: TestClient) -> None:
    ids = create_project(client)
    project_url = f"/api/v1/onboarding-projects/{ids['project_id']}"
    assert client.get(project_url).json()["stage"] == "awaiting_source_schema"
    _upload(client, ids["project_id"], "firm-a")
    assert client.get(project_url).json()["stage"] == "awaiting_target_inspection"

    adapter = StubAdapter(TargetInspection(normalized=_target_snapshot(), config_revision=0))
    _use_adapter(adapter)
    first = client.post(f"{project_url}/inspect-target")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True and body["observed_config_revision"] == 0
    assert body["schema_version"]["target_config_revision"] == 0
    assert body["schema_version"]["supported_transformations"]["normalize_phone"] == ["phone"]
    phone = next(f for f in body["schema_version"]["fields"] if f["key"] == "contact.phone_number")
    assert phone["canonical_ref"] == "client.phone_number"

    again = client.post(f"{project_url}/inspect-target")
    assert again.status_code == 200 and again.json()["created"] is False
    project = client.get(project_url).json()
    assert project["stage"] == "ready_for_mapping"
    assert project["latest_target_schema"]["version"] == 1
    assert adapter.calls == 2


def test_inspect_target_connector_failure_stores_nothing(client: TestClient, db: Session) -> None:
    ids = create_project(client)
    _use_adapter(StubAdapter(ConnectorError("authorization", "Rejected.")))
    response = client.post(f"/api/v1/onboarding-projects/{ids['project_id']}/inspect-target")
    assert response.status_code == 502
    assert response.json()["error"]["details"][0] == {"category": "authorization"}
    assert db.execute(text("SELECT count(*) FROM schema_definitions")).scalar_one() == 0


def test_missing_credentials_are_reported_not_guessed(client: TestClient) -> None:
    ids = create_project(client, "firm-q")  # no ONBOARDING_SECRET_FIRM_Q_* variables exist
    response = client.post(f"/api/v1/onboarding-projects/{ids['project_id']}/inspect-target")
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["details"][0] == {"category": "configuration"}
    assert error["details"][1]["code"] == "missing_credentials"


def test_unknown_project_and_schema(client: TestClient) -> None:
    assert client.get("/api/v1/onboarding-projects/999").status_code == 404
    a = create_project(client, "firm-a")
    b = create_project(client, "firm-b")
    created = _upload(client, a["project_id"], "firm-a").json()  # type: ignore[attr-defined]
    schema_id = created["schema_version"]["id"]
    # A schema is only reachable through the project that owns it.
    other = client.get(f"/api/v1/onboarding-projects/{b['project_id']}/schemas/{schema_id}")
    assert other.status_code == 404


def test_connector_secrets_are_not_stored(client: TestClient, db: Session) -> None:
    create_project(client)
    connector = db.scalar(select(CMSConnector))
    assert connector is not None
    columns = {c.name for c in CMSConnector.__table__.columns}
    assert not {"password", "secret", "token"} & columns
    assert connector.credential_ref == "firm-a-mock-cms"
