"""Test fixtures. Integration tests use a real PostgreSQL database (MOCK_CMS_TEST_DATABASE_URL)."""

import os
from collections.abc import Iterator
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "MOCK_CMS_TEST_DATABASE_URL",
    "postgresql+psycopg://lcms:lcms_dev_password@localhost:5434/mock_cms_test",
)
# Must be set before app settings are first read.
os.environ["MOCK_CMS_DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.permissions import Role  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.config_service import create_tenant  # noqa: E402

PASSWORD = "test-password"
ROOT = Path(__file__).resolve().parent.parent
TABLES = "intake_records, intake_mappings, auth_sessions, users, field_definitions, tenants"


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    get_engine().dispose()


@pytest.fixture(scope="session")
def password_hash() -> str:
    # Hash once; scrypt is deliberately slow.
    return hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def seeded(password_hash: str) -> None:
    """Fresh data per test: firms A and B, one user per role each (e.g. 'a.admin', 'b.staff')."""
    with get_sessionmaker()() as db:
        db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        for prefix, slug, name in (("a", "firm-a", "Firm A"), ("b", "firm-b", "Firm B")):
            tenant = create_tenant(db, slug, name)
            for role in Role:
                db.add(
                    User(
                        tenant_id=tenant.id,
                        username=f"{prefix}.{role.value}",
                        display_name=f"{name} {role.value}",
                        role=role.value,
                        password_hash=password_hash,
                    )
                )
        db.commit()


@pytest.fixture
def db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/token", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin_a(client: TestClient) -> dict[str, str]:
    return login(client, "a.admin")
