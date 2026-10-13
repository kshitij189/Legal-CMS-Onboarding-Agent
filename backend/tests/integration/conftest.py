from collections.abc import Iterator
from dataclasses import dataclass

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

TABLES = (
    "field_definitions, schema_definitions, onboarding_projects, cms_connectors, auth_tokens, "
    "users, firms"
)
PASSWORD = "test-password"


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


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dataclass
class FirmContext:
    slug: str
    admin: dict[str, str]
    specialist: dict[str, str]
    connector_id: int
    project_id: int


def setup_firm(
    client: TestClient, slug: str = "firm-a", base_url: str = "http://localhost:8100"
) -> FirmContext:
    """Sign up a firm, add a specialist, a mock CMS connector and an onboarding project."""
    signup = client.post(
        "/api/v1/firms",
        json={
            "firm": {"slug": slug, "name": slug.upper()},
            "admin": {"username": f"{slug}.admin", "display_name": "Admin", "password": PASSWORD},
        },
    )
    assert signup.status_code == 201, signup.text
    admin = login(client, f"{slug}.admin")
    specialist = client.post(
        "/api/v1/users",
        headers=admin,
        json={
            "username": f"{slug}.specialist",
            "display_name": "Specialist",
            "password": PASSWORD,
            "role": "integration_specialist",
        },
    )
    assert specialist.status_code == 201, specialist.text
    connector = client.post(
        "/api/v1/connectors",
        headers=admin,
        json={
            "name": "Mock CMS",
            "connector_type": "mock_cms_api",
            "base_url": base_url,
            "credential_ref": f"{slug}-mock-cms",
        },
    )
    assert connector.status_code == 201, connector.text
    project = client.post(
        "/api/v1/onboarding-projects",
        headers=admin,
        json={"connector_id": connector.json()["id"], "name": f"{slug} onboarding"},
    )
    assert project.status_code == 201, project.text
    return FirmContext(
        slug=slug,
        admin=admin,
        specialist=login(client, f"{slug}.specialist"),
        connector_id=connector.json()["id"],
        project_id=project.json()["id"],
    )
