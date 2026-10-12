from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.session import get_engine, get_sessionmaker
from app.main import app
from tests.conftest import TEST_DATABASE_URL

TABLES = "field_definitions, schema_definitions, onboarding_projects, cms_connectors, firms"


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with get_sessionmaker()() as db:
        db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        db.commit()


@pytest.fixture
def db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_project(client: TestClient, slug: str = "firm-a") -> dict[str, int]:
    firm = client.post("/api/v1/firms", json={"slug": slug, "name": slug.upper()})
    assert firm.status_code == 201, firm.text
    connector = client.post(
        f"/api/v1/firms/{firm.json()['id']}/connectors",
        json={
            "name": "Mock CMS",
            "connector_type": "mock_cms_api",
            "base_url": "http://localhost:8100",
            "credential_ref": f"{slug}-mock-cms",
        },
    )
    assert connector.status_code == 201, connector.text
    project = client.post(
        "/api/v1/onboarding-projects",
        json={
            "firm_id": firm.json()["id"],
            "connector_id": connector.json()["id"],
            "name": f"{slug} onboarding",
        },
    )
    assert project.status_code == 201, project.text
    return {
        "firm_id": firm.json()["id"],
        "connector_id": connector.json()["id"],
        "project_id": project.json()["id"],
    }
