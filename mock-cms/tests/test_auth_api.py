from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthSession, User, utcnow
from app.security import hash_token
from tests.conftest import PASSWORD, login


def test_token_login_and_me(client: TestClient) -> None:
    headers = login(client, "a.integration")
    body = client.get("/api/v1/me", headers=headers).json()
    assert body["username"] == "a.integration"
    assert body["tenant"]["slug"] == "firm-a"
    assert "mappings:write" in body["permissions"]
    assert "records:read" not in body["permissions"]


def test_bad_credentials_return_structured_401(client: TestClient) -> None:
    response = client.post("/api/v1/auth/token", json={"username": "a.admin", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    unknown = client.post("/api/v1/auth/token", json={"username": "ghost", "password": PASSWORD})
    assert unknown.status_code == 401


def test_missing_and_invalid_tokens_are_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    bogus = client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-token"})
    assert bogus.status_code == 401
    assert bogus.headers["WWW-Authenticate"] == "Bearer"


def test_token_is_stored_hashed(client: TestClient, db: Session) -> None:
    token = login(client, "a.admin")["Authorization"].removeprefix("Bearer ")
    stored = db.scalars(select(AuthSession.token_hash)).all()
    assert hash_token(token) in stored
    assert token not in stored


def test_logout_revokes_token(client: TestClient) -> None:
    headers = login(client, "a.admin")
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_expired_token_is_rejected(client: TestClient, db: Session) -> None:
    headers = login(client, "a.admin")
    db.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_deactivated_user_cannot_use_existing_token(client: TestClient, db: Session) -> None:
    headers = login(client, "a.admin")
    db.execute(update(User).where(User.username == "a.admin").values(is_active=False))
    db.commit()
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    retry = client.post("/api/v1/auth/token", json={"username": "a.admin", "password": PASSWORD})
    assert retry.status_code == 401
