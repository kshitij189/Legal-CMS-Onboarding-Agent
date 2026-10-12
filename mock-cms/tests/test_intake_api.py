from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import login

H = dict[str, str]

MAPPINGS = {
    "client_name": {"target_field": "contact.full_name", "transformation": "trim"},
    "client_mobile": {"target_field": "contact.phone_number", "transformation": "normalize_phone"},
    "client_email": {"target_field": "contact.email", "transformation": "lowercase"},
    "matter_type": {
        "target_field": "matter.practice_area",
        "value_map": {"Divorce": "family_law", "Workplace": "employment"},
    },
}


def _configure(client: TestClient, headers: H) -> None:
    for source, body in MAPPINGS.items():
        response = client.put(
            f"/api/v1/config/intake-mappings/{source}", headers=headers, json=body
        )
        assert response.status_code == 200, response.text


def test_submission_creates_record_with_transformed_values(client: TestClient, db: Session) -> None:
    _configure(client, login(client, "a.integration"))
    intake = login(client, "a.intake_coordinator")
    response = client.post(
        "/api/v1/intake-submissions",
        headers=intake,
        json={
            "values": {
                "client_name": "  Jane Doe ",
                "client_mobile": "+1 (555) 123-4567",
                "client_email": "Jane@Example.com",
                "matter_type": "Workplace",
                "referral_source": "web",
            }
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["values"] == {
        "contact.full_name": "Jane Doe",
        "contact.phone_number": "+15551234567",
        "contact.email": "jane@example.com",
        "matter.practice_area": "employment",
    }
    assert record["unmapped_keys"] == ["referral_source"]
    assert record["config_revision"] == 4
    assert record["submitted_by"] == "a.intake_coordinator"

    stored = db.execute(
        text("SELECT values FROM intake_records WHERE id = :id"), {"id": record["id"]}
    )
    assert stored.scalar_one()["contact.phone_number"] == "+15551234567"
    listed = client.get("/api/v1/intake-records", headers=intake).json()
    assert [r["id"] for r in listed] == [record["id"]]


def test_submission_validation_errors_are_structured_and_nothing_is_saved(
    client: TestClient, db: Session
) -> None:
    admin = login(client, "a.admin")
    _configure(client, admin)
    client.patch("/api/v1/config/fields/contact.email", headers=admin, json={"required": True})
    response = client.post(
        "/api/v1/intake-submissions",
        headers=admin,
        json={"values": {"client_name": "Jane", "client_mobile": "12", "matter_type": "Tax"}},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    codes = {d["field"]: d["code"] for d in error["details"]}
    assert codes == {
        "contact.phone_number": "invalid_phone",
        "matter.practice_area": "invalid_option",
        "contact.email": "required_field_missing",
    }
    assert db.execute(text("SELECT count(*) FROM intake_records")).scalar_one() == 0


def test_required_field_without_mapping_is_reported(client: TestClient) -> None:
    admin = login(client, "a.admin")
    response = client.post(
        "/api/v1/intake-submissions", headers=admin, json={"values": {"anything": "x"}}
    )
    assert response.status_code == 422
    details = {d["field"]: d for d in response.json()["error"]["details"]}
    assert set(details) == {"contact.full_name", "matter.practice_area"}
    assert all(d["source_key"] is None for d in details.values())


def test_submission_rejects_non_scalar_values(client: TestClient) -> None:
    admin = login(client, "a.admin")
    response = client.post(
        "/api/v1/intake-submissions", headers=admin, json={"values": {"client_name": {"a": 1}}}
    )
    assert response.status_code == 422
