from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CMSConnector, User
from app.seed import seed
from tests.integration.conftest import login


def test_seed_is_idempotent_and_logins_work(client: TestClient, db: Session) -> None:
    created = seed(db, "seed-password", "http://localhost:8100")
    assert "firm firm-c" in created and "user c.specialist" in created
    assert seed(db, "seed-password", "http://localhost:8100") == []
    assert db.scalar(select(func.count()).select_from(User)) == 6
    assert db.scalar(select(func.count()).select_from(CMSConnector)) == 3

    me = client.get("/api/v1/auth/me", headers=login(client, "b.specialist", "seed-password"))
    assert me.json()["firm"]["slug"] == "firm-b"
    assert me.json()["user"]["role"] == "integration_specialist"
