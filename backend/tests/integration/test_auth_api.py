"""Reviewer authentication and role permissions (decision D5)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthToken, Firm, User, utcnow
from app.security import hash_token
from tests.integration.conftest import PASSWORD, login, setup_firm

PROJECT_ENDPOINTS = [
    ("GET", "/api/v1/onboarding-projects/{p}", None),
    ("GET", "/api/v1/onboarding-projects/{p}/schemas", None),
    ("POST", "/api/v1/onboarding-projects/{p}/inspect-target", None),
    (
        "POST",
        "/api/v1/onboarding-projects/{p}/source-schema",
        {"format": "html_form", "content": "<input name='x'>"},
    ),
]


def test_every_api_endpoint_requires_a_token(client: TestClient) -> None:
    ctx = setup_firm(client)
    endpoints = [
        ("GET", "/api/v1/firm", None),
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {}),
        ("GET", "/api/v1/connectors", None),
        ("POST", "/api/v1/connectors", {}),
        ("POST", "/api/v1/onboarding-projects", {}),
        ("GET", "/api/v1/auth/me", None),
        *PROJECT_ENDPOINTS,
    ]
    for method, path, body in endpoints:
        response = client.request(method, path.format(p=ctx.project_id), json=body)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["error"]["code"] == "unauthenticated"
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_signup_creates_firm_admin_and_rejects_duplicates(client: TestClient) -> None:
    ctx = setup_firm(client)
    me = client.get("/api/v1/auth/me", headers=ctx.admin).json()
    assert me["user"]["role"] == "firm_admin"
    assert me["firm"]["slug"] == "firm-a"
    assert "users:manage" in me["permissions"]
    again = client.post(
        "/api/v1/firms",
        json={
            "firm": {"slug": "firm-a", "name": "Again"},
            "admin": {"username": "someone", "display_name": "X", "password": PASSWORD},
        },
    )
    assert again.status_code == 409
    short_password = client.post(
        "/api/v1/firms",
        json={
            "firm": {"slug": "firm-z", "name": "Z"},
            "admin": {"username": "z.admin", "display_name": "Z", "password": "short"},
        },
    )
    assert short_password.status_code == 422


def test_signup_can_be_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONBOARDING_ALLOW_SIGNUP", "false")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/firms",
            json={
                "firm": {"slug": "firm-z", "name": "Z"},
                "admin": {"username": "z.admin", "display_name": "Z", "password": PASSWORD},
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["details"][0]["code"] == "signup_disabled"
    finally:
        monkeypatch.delenv("ONBOARDING_ALLOW_SIGNUP")
        get_settings.cache_clear()


def test_specialist_permissions(client: TestClient) -> None:
    ctx = setup_firm(client)
    me = client.get("/api/v1/auth/me", headers=ctx.specialist).json()
    assert me["user"]["role"] == "integration_specialist"
    forbidden = [
        ("GET", "/api/v1/users", None),
        (
            "POST",
            "/api/v1/users",
            {"username": "x.y.z", "display_name": "X", "password": PASSWORD, "role": "firm_admin"},
        ),
        (
            "POST",
            "/api/v1/connectors",
            {
                "name": "n",
                "connector_type": "mock_cms_api",
                "base_url": "http://localhost:8100",
                "credential_ref": "firm-a-x",
            },
        ),
        ("POST", "/api/v1/onboarding-projects", {"connector_id": ctx.connector_id, "name": "n"}),
    ]
    for method, path, body in forbidden:
        response = client.request(method, path, headers=ctx.specialist, json=body)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["details"][0]["code"] == "missing_permission"
    # Allowed: read the firm, connectors and project; upload schemas; inspect (tested elsewhere).
    assert client.get("/api/v1/firm", headers=ctx.specialist).status_code == 200
    assert client.get("/api/v1/connectors", headers=ctx.specialist).status_code == 200
    project_url = f"/api/v1/onboarding-projects/{ctx.project_id}"
    assert client.get(project_url, headers=ctx.specialist).status_code == 200


def test_admin_manages_only_own_firm_users(client: TestClient) -> None:
    a = setup_firm(client, "firm-a")
    setup_firm(client, "firm-b")
    users = client.get("/api/v1/users", headers=a.admin).json()
    assert {u["username"] for u in users} == {"firm-a.admin", "firm-a.specialist"}
    duplicate = client.post(
        "/api/v1/users",
        headers=a.admin,
        json={
            "username": "firm-b.admin",
            "display_name": "Impostor",
            "password": PASSWORD,
            "role": "firm_admin",
        },
    )
    assert duplicate.status_code == 409


def test_bad_credentials_and_hashed_tokens(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    bad = client.post("/api/v1/auth/token", json={"username": "firm-a.admin", "password": "nope"})
    assert bad.status_code == 401
    unknown = client.post("/api/v1/auth/token", json={"username": "ghost", "password": PASSWORD})
    assert unknown.json()["error"]["message"] == bad.json()["error"]["message"]
    token = ctx.admin["Authorization"].removeprefix("Bearer ")
    stored = db.scalars(select(AuthToken.token_hash)).all()
    assert hash_token(token) in stored and token not in stored
    password_hashes = db.scalars(select(User.password_hash)).all()
    assert all(h.startswith("scrypt$") for h in password_hashes)


def test_logout_expiry_and_deactivation(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    assert client.post("/api/v1/auth/logout", headers=ctx.admin).status_code == 204
    assert client.get("/api/v1/auth/me", headers=ctx.admin).status_code == 401

    db.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/v1/auth/me", headers=ctx.specialist).status_code == 401

    fresh = login(client, "firm-a.specialist")
    db.execute(update(User).where(User.username == "firm-a.specialist").values(is_active=False))
    db.commit()
    assert client.get("/api/v1/auth/me", headers=fresh).status_code == 401
    retry = client.post(
        "/api/v1/auth/token", json={"username": "firm-a.specialist", "password": PASSWORD}
    )
    assert retry.status_code == 401


def test_deactivated_firm_locks_out_its_users(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    db.execute(update(Firm).where(Firm.slug == "firm-a").values(is_active=False))
    db.commit()
    assert client.get("/api/v1/auth/me", headers=ctx.admin).status_code == 401


def test_creator_is_recorded(client: TestClient, db: Session) -> None:
    ctx = setup_firm(client)
    row = db.execute(
        text(
            "SELECT u.username FROM onboarding_projects p JOIN users u ON u.id = p.created_by_id "
            "WHERE p.id = :p"
        ),
        {"p": ctx.project_id},
    ).scalar_one()
    assert row == "firm-a.admin"
