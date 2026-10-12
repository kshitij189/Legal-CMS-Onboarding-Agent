from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import login

# (method, path, json body, permission it requires)
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/v1/schema", None, "schema:read"),
    ("GET", "/api/v1/config", None, "config:read"),
    ("GET", "/api/v1/config/intake-mappings", None, "config:read"),
    ("PATCH", "/api/v1/config/fields/contact.email", {"required": True}, "fields:write"),
    (
        "POST",
        "/api/v1/config/custom-fields",
        {"entity": "contact", "name": "nickname", "label": "Nickname", "data_type": "string"},
        "fields:write",
    ),
    (
        "PUT",
        "/api/v1/config/intake-mappings/client_email",
        {"target_field": "contact.email"},
        "mappings:write",
    ),
    ("DELETE", "/api/v1/config/intake-mappings/missing", None, "mappings:write"),
    ("POST", "/api/v1/intake-submissions", {"values": {}}, "intake:submit"),
    ("GET", "/api/v1/intake-records", None, "records:read"),
]

GRANTS = {
    "admin": {
        "schema:read",
        "config:read",
        "fields:write",
        "mappings:write",
        "intake:submit",
        "records:read",
    },
    "integration": {"schema:read", "config:read", "fields:write", "mappings:write"},
    "intake_coordinator": {"schema:read", "intake:submit", "records:read"},
    "staff": {"schema:read", "config:read", "records:read"},
}


@pytest.mark.parametrize("role", list(GRANTS))
def test_permission_matrix(client: TestClient, role: str) -> None:
    headers = login(client, f"a.{role}")
    for method, path, body, permission in ENDPOINTS:
        response = client.request(method, path, headers=headers, json=body)
        if permission in GRANTS[role]:
            assert response.status_code != 403, (role, method, path, response.text)
        else:
            assert response.status_code == 403, (role, method, path, response.text)
            assert response.json()["error"]["details"][0]["permission"] == permission


def test_forbidden_write_changes_nothing(client: TestClient, db: Session) -> None:
    staff = login(client, "a.staff")
    client.patch("/api/v1/config/fields/contact.email", headers=staff, json={"required": True})
    row = db.execute(
        text(
            "SELECT f.required, t.config_revision FROM field_definitions f "
            "JOIN tenants t ON t.id = f.tenant_id WHERE t.slug='firm-a' AND f.key='contact.email'"
        )
    ).one()
    assert tuple(row) == (False, 0)


def test_tenants_are_isolated(client: TestClient) -> None:
    a, b = login(client, "a.admin"), login(client, "b.admin")
    client.put(
        "/api/v1/config/intake-mappings/client_email",
        headers=a,
        json={"target_field": "contact.email"},
    )
    client.patch("/api/v1/config/fields/contact.email", headers=a, json={"required": True})
    client.post(
        "/api/v1/intake-submissions",
        headers=a,
        json={"values": {"client_email": "x@example.com"}},
    )

    b_config = client.get("/api/v1/config", headers=b).json()
    assert b_config["tenant"]["slug"] == "firm-b"
    assert b_config["config_revision"] == 0
    assert b_config["intake_mappings"] == []
    assert next(f for f in b_config["fields"] if f["key"] == "contact.email")["required"] is False
    assert client.get("/api/v1/intake-records", headers=b).json() == []
    # Firm B cannot delete Firm A's mapping by guessing its key.
    assert (
        client.delete("/api/v1/config/intake-mappings/client_email", headers=b).status_code == 404
    )
    assert len(client.get("/api/v1/config/intake-mappings", headers=a).json()) == 1


def test_records_from_other_tenant_are_not_found(client: TestClient) -> None:
    a = login(client, "a.admin")
    client.put(
        "/api/v1/config/intake-mappings/name",
        headers=a,
        json={"target_field": "contact.full_name"},
    )
    client.put(
        "/api/v1/config/intake-mappings/area",
        headers=a,
        json={"target_field": "matter.practice_area"},
    )
    created = client.post(
        "/api/v1/intake-submissions",
        headers=a,
        json={"values": {"name": "Jane Doe", "area": "employment"}},
    )
    assert created.status_code == 201
    record_id = created.json()["id"]
    b = login(client, "b.admin")
    assert client.get(f"/api/v1/intake-records/{record_id}", headers=b).status_code == 404
    assert client.get(f"/api/v1/intake-records/{record_id}", headers=a).status_code == 200


def test_database_rejects_cross_tenant_mapping(db: Session) -> None:
    """Defence in depth: a mapping cannot point at another tenant's field even via raw SQL."""
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO intake_mappings (tenant_id, source_key, target_field_id, "
                "transformation, updated_at) "
                "SELECT ta.id, 'leak', fb.id, 'none', now() FROM tenants ta, field_definitions fb "
                "JOIN tenants tb ON tb.id = fb.tenant_id "
                "WHERE ta.slug='firm-a' AND tb.slug='firm-b' AND fb.key='contact.email'"
            )
        )
    db.rollback()
