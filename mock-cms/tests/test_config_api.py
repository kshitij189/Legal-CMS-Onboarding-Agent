"""Configuration API: every write is checked by reading it back through the API and the database."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

H = dict[str, str]


def _config(client: TestClient, headers: H) -> dict[str, Any]:
    response = client.get("/api/v1/config", headers=headers)
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def _field(config: dict[str, Any], key: str) -> dict[str, Any]:
    return next(f for f in config["fields"] if f["key"] == key)


def _db_field(db: Session, slug: str, key: str) -> Any:
    return db.execute(
        text(
            "SELECT f.required, f.label, f.options, f.enabled, t.config_revision "
            "FROM field_definitions f JOIN tenants t ON t.id = f.tenant_id "
            "WHERE t.slug = :slug AND f.key = :key"
        ),
        {"slug": slug, "key": key},
    ).one()


def test_schema_lists_builtin_fields_and_transformations(client: TestClient, admin_a: H) -> None:
    body = client.get("/api/v1/schema", headers=admin_a).json()
    keys = {f["key"] for f in body["fields"]}
    assert {"contact.full_name", "contact.phone_number", "matter.practice_area"} <= keys
    assert body["config_revision"] == 0
    transformations = {t["name"]: t["applicable_types"] for t in body["transformations"]}
    assert transformations["normalize_phone"] == ["phone"]
    assert body["entities"] == ["contact", "matter"]


def test_update_field_persists_and_bumps_revision(
    client: TestClient, admin_a: H, db: Session
) -> None:
    response = client.patch(
        "/api/v1/config/fields/contact.phone_number",
        headers=admin_a,
        json={"required": True, "label": "Mobile phone"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["config_revision"] == 1

    # Read-after-write through the API ...
    field = _field(_config(client, admin_a), "contact.phone_number")
    assert field["required"] is True
    assert field["label"] == "Mobile phone"
    # ... and directly in the database.
    row = _db_field(db, "firm-a", "contact.phone_number")
    assert (row.required, row.label, row.config_revision) == (True, "Mobile phone", 1)


def test_repeated_identical_update_is_a_no_op(client: TestClient, admin_a: H) -> None:
    payload = {"required": True}
    first = client.patch("/api/v1/config/fields/contact.email", headers=admin_a, json=payload)
    second = client.patch("/api/v1/config/fields/contact.email", headers=admin_a, json=payload)
    assert first.json()["changed"] is True
    assert second.json()["changed"] is False
    assert second.json()["config_revision"] == first.json()["config_revision"] == 1


def test_update_enum_options(client: TestClient, admin_a: H, db: Session) -> None:
    options = ["family_law", "employment", "tax"]
    response = client.patch(
        "/api/v1/config/fields/matter.practice_area", headers=admin_a, json={"options": options}
    )
    assert response.status_code == 200
    assert _db_field(db, "firm-a", "matter.practice_area").options == options


def test_field_update_validation_errors(client: TestClient, admin_a: H) -> None:
    unknown = client.patch("/api/v1/config/fields/contact.nope", headers=admin_a, json={})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["details"][0]["code"] == "unknown_field"

    options_on_string = client.patch(
        "/api/v1/config/fields/contact.full_name", headers=admin_a, json={"options": ["x"]}
    )
    assert options_on_string.status_code == 422
    assert options_on_string.json()["error"]["details"][0]["code"] == "options_not_allowed"

    duplicate = client.patch(
        "/api/v1/config/fields/matter.practice_area", headers=admin_a, json={"options": ["a", "a"]}
    )
    assert duplicate.json()["error"]["details"][0]["code"] == "duplicate_option"

    extra = client.patch(
        "/api/v1/config/fields/contact.email", headers=admin_a, json={"data_type": "phone"}
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_error"


def test_create_custom_field(client: TestClient, admin_a: H, db: Session) -> None:
    payload = {
        "entity": "matter",
        "name": "review_status",
        "label": "Review status",
        "data_type": "enum",
        "required": True,
        "options": ["pending", "reviewed"],
    }
    response = client.post("/api/v1/config/custom-fields", headers=admin_a, json=payload)
    assert response.status_code == 201
    assert response.json()["field"]["key"] == "matter.custom.review_status"
    assert response.json()["field"]["is_custom"] is True
    row = _db_field(db, "firm-a", "matter.custom.review_status")
    assert row.required is True and row.options == ["pending", "reviewed"]

    duplicate = client.post("/api/v1/config/custom-fields", headers=admin_a, json=payload)
    assert duplicate.status_code == 409


def test_custom_field_validation(client: TestClient, admin_a: H) -> None:
    base = {"entity": "contact", "name": "nickname", "label": "Nickname"}
    enum_without_options = client.post(
        "/api/v1/config/custom-fields", headers=admin_a, json={**base, "data_type": "enum"}
    )
    assert enum_without_options.json()["error"]["details"][0]["code"] == "options_required"
    bad_name = client.post(
        "/api/v1/config/custom-fields",
        headers=admin_a,
        json={**base, "name": "Bad Name!", "data_type": "string"},
    )
    assert bad_name.status_code == 422
    bad_entity = client.post(
        "/api/v1/config/custom-fields",
        headers=admin_a,
        json={**base, "entity": "invoice", "data_type": "string"},
    )
    assert bad_entity.status_code == 422


def test_mapping_upsert_read_back_and_idempotency(
    client: TestClient, admin_a: H, db: Session
) -> None:
    payload = {"target_field": "contact.phone_number", "transformation": "normalize_phone"}
    first = client.put(
        "/api/v1/config/intake-mappings/client_mobile", headers=admin_a, json=payload
    )
    assert first.status_code == 200
    assert first.json()["changed"] is True
    second = client.put(
        "/api/v1/config/intake-mappings/client_mobile", headers=admin_a, json=payload
    )
    assert second.json()["changed"] is False
    assert second.json()["config_revision"] == 1

    mappings = client.get("/api/v1/config/intake-mappings", headers=admin_a).json()
    assert [(m["source_key"], m["target_field"], m["transformation"]) for m in mappings] == [
        ("client_mobile", "contact.phone_number", "normalize_phone")
    ]
    row = db.execute(
        text(
            "SELECT m.source_key, f.key, m.transformation, m.value_map IS NULL, "
            "f.options IS NULL FROM intake_mappings m "
            "JOIN field_definitions f ON f.id = m.target_field_id"
        )
    ).one()
    # Absent JSON values are SQL NULL (not JSON 'null'), so IS NULL queries behave.
    assert tuple(row) == ("client_mobile", "contact.phone_number", "normalize_phone", True, True)


def test_mapping_can_be_retargeted_and_deleted(client: TestClient, admin_a: H) -> None:
    url = "/api/v1/config/intake-mappings/contact_info"
    client.put(url, headers=admin_a, json={"target_field": "contact.email"})
    retarget = client.put(
        url, headers=admin_a, json={"target_field": "contact.callback_preference"}
    )
    assert retarget.json()["mapping"]["target_field"] == "contact.callback_preference"
    deleted = client.delete(url, headers=admin_a)
    assert deleted.status_code == 200
    assert deleted.json()["config_revision"] == 3
    assert client.get("/api/v1/config/intake-mappings", headers=admin_a).json() == []
    assert client.delete(url, headers=admin_a).status_code == 404


def test_mapping_validation_rejections(client: TestClient, admin_a: H) -> None:
    def put(source: str, body: dict[str, Any]) -> Any:
        return client.put(f"/api/v1/config/intake-mappings/{source}", headers=admin_a, json=body)

    unknown = put("x", {"target_field": "contact.shoe_size"})
    assert (unknown.status_code, unknown.json()["error"]["details"][0]["code"]) == (
        422,
        "unknown_field",
    )
    incompatible = put("x", {"target_field": "contact.email", "transformation": "normalize_phone"})
    assert incompatible.json()["error"]["details"][0]["code"] == "unsupported_transformation"
    unsupported = put("x", {"target_field": "contact.email", "transformation": "uppercase"})
    assert unsupported.status_code == 422
    value_map_on_string = put("x", {"target_field": "matter.jurisdiction", "value_map": {"a": "b"}})
    assert value_map_on_string.json()["error"]["details"][0]["code"] == "value_map_requires_enum"
    bad_option = put(
        "x", {"target_field": "matter.practice_area", "value_map": {"Divorce": "divorce"}}
    )
    assert bad_option.json()["error"]["details"][0]["code"] == "invalid_enum_option"
    bad_key = put("bad key!", {"target_field": "contact.email"})
    assert bad_key.json()["error"]["details"][0]["code"] == "invalid_source_key"
    assert _config(client, admin_a)["config_revision"] == 0


def test_target_field_can_only_be_mapped_once(client: TestClient, admin_a: H) -> None:
    body = {"target_field": "contact.email"}
    client.put("/api/v1/config/intake-mappings/email", headers=admin_a, json=body)
    clash = client.put("/api/v1/config/intake-mappings/email_address", headers=admin_a, json=body)
    assert clash.status_code == 409
    assert clash.json()["error"]["details"][0]["code"] == "target_already_mapped"


def test_mapped_field_cannot_be_disabled_or_lose_mapped_options(
    client: TestClient, admin_a: H
) -> None:
    client.put(
        "/api/v1/config/intake-mappings/case_type",
        headers=admin_a,
        json={"target_field": "matter.practice_area", "value_map": {"Workplace": "employment"}},
    )
    disable = client.patch(
        "/api/v1/config/fields/matter.practice_area", headers=admin_a, json={"enabled": False}
    )
    assert disable.status_code == 409
    assert disable.json()["error"]["details"][0]["code"] == "field_in_use"
    drop_option = client.patch(
        "/api/v1/config/fields/matter.practice_area",
        headers=admin_a,
        json={"options": ["family_law"]},
    )
    assert drop_option.status_code == 409
    assert drop_option.json()["error"]["details"][0]["code"] == "option_in_use"


def test_disabled_field_hidden_from_schema_and_not_mappable(client: TestClient, admin_a: H) -> None:
    client.patch("/api/v1/config/fields/matter.summary", headers=admin_a, json={"enabled": False})
    schema_keys = {f["key"] for f in client.get("/api/v1/schema", headers=admin_a).json()["fields"]}
    assert "matter.summary" not in schema_keys
    mapping = client.put(
        "/api/v1/config/intake-mappings/notes",
        headers=admin_a,
        json={"target_field": "matter.summary"},
    )
    assert mapping.json()["error"]["details"][0]["code"] == "target_disabled"
