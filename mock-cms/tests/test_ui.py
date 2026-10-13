"""HTML UI: manual configuration through forms, with CSRF and role checks."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ui import SESSION_COOKIE
from tests.conftest import PASSWORD


def _ui_login(client: TestClient, username: str) -> None:
    response = client.post(
        "/login", data={"username": username, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    assert SESSION_COOKIE in response.cookies


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def test_unauthenticated_pages_redirect_to_login(client: TestClient) -> None:
    for path in ("/config/fields", "/config/mappings", "/intake/new", "/records"):
        response = client.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_login_page_and_bad_credentials(client: TestClient) -> None:
    assert '<label for="username">Username</label>' in client.get("/login").text
    bad = client.post("/login", data={"username": "a.admin", "password": "wrong"})
    assert bad.status_code == 401
    assert 'role="alert"' in bad.text
    assert SESSION_COOKIE not in bad.cookies


def test_session_cookie_is_httponly(client: TestClient) -> None:
    response = client.post(
        "/login", data={"username": "a.admin", "password": PASSWORD}, follow_redirects=False
    )
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header


def test_edit_field_through_form(client: TestClient, db: Session) -> None:
    _ui_login(client, "a.admin")
    page = client.get("/config/fields/contact.phone_number/edit")
    assert page.status_code == 200
    response = client.post(
        "/config/fields/contact.phone_number",
        data={"csrf_token": _csrf(page.text), "label": "Mobile", "required": "on", "enabled": "on"},
    )
    assert response.status_code == 200
    assert "Saved. Configuration revision 1." in response.text
    row = db.execute(
        text(
            "SELECT label, required FROM field_definitions WHERE key='contact.phone_number' "
            "AND tenant_id=(SELECT id FROM tenants WHERE slug='firm-a')"
        )
    ).one()
    assert tuple(row) == ("Mobile", True)


def test_form_post_without_csrf_is_rejected(client: TestClient, db: Session) -> None:
    _ui_login(client, "a.admin")
    response = client.post(
        "/config/fields/contact.email",
        data={"csrf_token": "forged", "label": "Hacked", "required": "on", "enabled": "on"},
    )
    assert response.status_code == 400
    revision = db.execute(text("SELECT config_revision FROM tenants WHERE slug='firm-a'"))
    assert revision.scalar_one() == 0


def test_create_custom_field_and_mapping_through_forms(client: TestClient, db: Session) -> None:
    _ui_login(client, "a.integration")
    page = client.get("/config/custom-fields/new")
    created = client.post(
        "/config/custom-fields",
        data={
            "csrf_token": _csrf(page.text),
            "entity": "matter",
            "name": "review_status",
            "label": "Review status",
            "data_type": "enum",
            "required": "on",
            "options": "pending\nreviewed",
        },
    )
    assert created.status_code == 200
    assert "matter.custom.review_status" in created.text

    mappings_page = client.get("/config/mappings")
    assert 'value="matter.custom.review_status"' in mappings_page.text
    saved = client.post(
        "/config/mappings",
        data={
            "csrf_token": _csrf(mappings_page.text),
            "source_key": "case_review",
            "target_field": "matter.custom.review_status",
            "transformation": "lowercase",
            "value_map": "awaiting = pending\ndone=reviewed",
        },
    )
    assert saved.status_code == 200
    assert "Saved. Configuration revision 2." in saved.text
    row = db.execute(
        text(
            "SELECT m.source_key, f.key, m.transformation, m.value_map FROM intake_mappings m "
            "JOIN field_definitions f ON f.id = m.target_field_id"
        )
    ).one()
    assert tuple(row) == (
        "case_review",
        "matter.custom.review_status",
        "lowercase",
        {"awaiting": "pending", "done": "reviewed"},
    )


def test_invalid_mapping_form_shows_errors(client: TestClient) -> None:
    _ui_login(client, "a.admin")
    page = client.get("/config/mappings")
    response = client.post(
        "/config/mappings",
        data={
            "csrf_token": _csrf(page.text),
            "source_key": "client_email",
            "target_field": "contact.email",
            "transformation": "normalize_phone",
        },
    )
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert "unsupported_transformation" in response.text
    # The submitted values are preserved for correction.
    assert 'value="client_email"' in response.text


def test_delete_mapping_through_form(client: TestClient) -> None:
    _ui_login(client, "a.admin")
    page = client.get("/config/mappings")
    client.post(
        "/config/mappings",
        data={
            "csrf_token": _csrf(page.text),
            "source_key": "client_email",
            "target_field": "contact.email",
            "transformation": "none",
        },
    )
    page = client.get("/config/mappings")
    assert 'aria-label="Delete mapping client_email"' in page.text
    deleted = client.post(
        "/config/mappings/client_email/delete", data={"csrf_token": _csrf(page.text)}
    )
    assert "Deleted." in deleted.text
    assert "No intake mappings configured." in deleted.text


def test_staff_can_view_but_not_edit(client: TestClient) -> None:
    _ui_login(client, "a.staff")
    fields = client.get("/config/fields")
    assert fields.status_code == 200
    assert "Edit" not in fields.text
    assert client.get("/config/fields/contact.email/edit").status_code == 403
    mappings = client.get("/config/mappings")
    assert "Add or update mapping" not in mappings.text


def test_intake_form_submission_and_records(client: TestClient) -> None:
    _ui_login(client, "a.admin")
    page = client.get("/config/mappings")
    token = _csrf(page.text)
    for source, target, transformation in (
        ("client_name", "contact.full_name", "trim"),
        ("matter_type", "matter.practice_area", "lowercase"),
        ("client_mobile", "contact.phone_number", "normalize_phone"),
    ):
        client.post(
            "/config/mappings",
            data={
                "csrf_token": token,
                "source_key": source,
                "target_field": target,
                "transformation": transformation,
            },
        )

    form = client.get("/intake/new")
    assert '<label for="v_client_mobile">client_mobile</label>' in form.text
    invalid = client.post(
        "/intake",
        data={
            "csrf_token": token,
            "v_client_name": "Jane",
            "v_matter_type": "Tax",
            "v_client_mobile": "12",
        },
    )
    assert invalid.status_code == 422
    assert "invalid_option" not in invalid.text  # human-readable messages, not codes
    assert "Expected one of" in invalid.text

    ok = client.post(
        "/intake",
        data={
            "csrf_token": token,
            "v_client_name": " Jane ",
            "v_matter_type": "Employment",
            "v_client_mobile": "(555) 123-4567",
        },
    )
    assert ok.status_code == 200
    assert "Intake record #1 created." in ok.text
    assert "5551234567" in ok.text


def test_logout_ends_session(client: TestClient) -> None:
    _ui_login(client, "a.admin")
    page = client.get("/config/fields")
    client.post("/logout", data={"csrf_token": _csrf(page.text)})
    response = client.get("/config/fields", follow_redirects=False)
    assert response.status_code == 303


def test_api_token_cannot_be_used_as_browser_session(client: TestClient) -> None:
    token = client.post(
        "/api/v1/auth/token", json={"username": "a.admin", "password": PASSWORD}
    ).json()["access_token"]
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/config/fields", follow_redirects=False).status_code == 303


def test_ui_variant_v2_renames_controls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    _ui_login(client, "a.admin")
    assert '<label for="source_key">Source field key</label>' in client.get("/config/mappings").text
    monkeypatch.setenv("MOCK_CMS_UI_VARIANT", "v2")
    get_settings.cache_clear()
    try:
        page = client.get("/config/mappings").text
        assert '<label for="source_key">External field identifier</label>' in page
        assert ">Store mapping</button>" in page and "Save mapping" not in page
        custom = client.get("/config/custom-fields/new").text
        assert '<label for="name">Internal name</label>' in custom
    finally:
        monkeypatch.delenv("MOCK_CMS_UI_VARIANT")
        get_settings.cache_clear()
