"""Server-rendered HTML UI. Uses the same services as the JSON API, so rules are identical.

Browser sessions use an HttpOnly cookie; every state-changing form carries a per-session CSRF token.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.catalog import DataType, Entity, Transformation
from app.config import get_settings
from app.db import get_db
from app.errors import CMSError
from app.models import AuthSession
from app.permissions import ROLE_PERMISSIONS, Permission, Role, has_permission
from app.schemas import CustomFieldCreate, FieldUpdate, MappingUpsert
from app.security import tokens_equal
from app.services import auth_service, config_service, intake_service

SESSION_COOKIE = "mockcms_session"
_VALUE_PREFIX = "v_"
# Visible text of form controls per UI variant. v2 is a "new release" with renamed controls; the
# JSON API is unchanged. Unknown variants fall back to v1.
UI_TEXT: dict[str, dict[str, str]] = {
    "v1": {
        "source_key": "Source field key",
        "save_mapping": "Save mapping",
        "custom_field_name": "Field name",
        "create_field": "Create field",
    },
    "v2": {
        "source_key": "External field identifier",
        "save_mapping": "Store mapping",
        "custom_field_name": "Internal name",
        "create_field": "Add field",
    },
}
_NOTICES = {"saved": "Saved.", "unchanged": "No changes were needed.", "deleted": "Deleted."}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


class LoginRequired(Exception):
    pass


class PageForbidden(Exception):
    def __init__(self, session: AuthSession, permission: Permission) -> None:
        self.session = session
        self.permission = permission


def _browser_session(request: Request, db: Session) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE)
    session = auth_service.resolve_session(db, token, kind="browser") if token else None
    if session is None:
        raise LoginRequired
    return session


def _guard(request: Request, db: Session, permission: Permission) -> AuthSession:
    session = _browser_session(request, db)
    if not has_permission(session.user.role, permission):
        raise PageForbidden(session, permission)
    return session


def _check_csrf(session: AuthSession, csrf_token: str) -> None:
    if not tokens_equal(session.csrf_token, csrf_token):
        raise CMSError("Invalid CSRF token.")


def _render(
    request: Request,
    template: str,
    session: AuthSession | None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    permissions = {p.value for p in ROLE_PERMISSIONS[Role(session.user.role)]} if session else set()
    notice = _NOTICES.get(request.query_params.get("notice", ""))
    revision = request.query_params.get("rev", "")
    if notice and revision.isdigit():
        notice = f"{notice} Configuration revision {revision}."
    return templates.TemplateResponse(
        request,
        template,
        {
            "session": session,
            "permissions": permissions,
            "notice": notice,
            "ui": UI_TEXT.get(get_settings().ui_variant, UI_TEXT["v1"]),
            **context,
        },
        status_code=status_code,
    )


def _errors(exc: CMSError | ValidationError) -> list[str]:
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    lines = [exc.message]
    for detail in exc.details:
        message = detail.get("message") or detail.get("code", "")
        lines.append(f"{detail.get('field') or detail.get('source_key', '')}: {message}")
    return lines


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_value_map(text: str) -> dict[str, str] | None:
    pairs = {}
    for line in _lines(text):
        source, sep, target = line.partition("=")
        if not sep:
            raise CMSError(f"Value map line '{line}' must look like source=target.")
        pairs[source.strip()] = target.strip()
    return pairs or None


def _redirect(path: str, notice: str, revision: int | None = None) -> RedirectResponse:
    query = f"?notice={notice}" + (f"&rev={revision}" if revision is not None else "")
    return RedirectResponse(path + query, status_code=303)


# --- authentication -----------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    return RedirectResponse("/config/fields", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return _render(request, "login.html", None)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    user = auth_service.authenticate(db, username, password)
    if user is None:
        return _render(
            request,
            "login.html",
            None,
            status_code=401,
            errors=["Invalid username or password."],
            username=username,
        )
    token, _ = auth_service.create_session(db, user, kind="browser")
    landing = "/config/fields" if has_permission(user.role, Permission.CONFIG_READ) else "/records"
    response = RedirectResponse(landing, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        max_age=get_settings().session_ttl_minutes * 60,
    )
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)) -> Response:
    session = _browser_session(request, db)
    _check_csrf(session, csrf_token)
    auth_service.revoke_session(db, session.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- field configuration ------------------------------------------------------------------------


@router.get("/config/fields", response_class=HTMLResponse)
def fields_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session = _guard(request, db, Permission.CONFIG_READ)
    tenant_id = session.user.tenant_id
    return _render(
        request,
        "fields.html",
        session,
        fields=config_service.list_fields(db, tenant_id),
        revision=config_service.current_revision(db, tenant_id),
    )


@router.get("/config/fields/{field_key}/edit", response_class=HTMLResponse)
def field_edit_page(field_key: str, request: Request, db: Session = Depends(get_db)) -> Response:
    session = _guard(request, db, Permission.FIELDS_WRITE)
    try:
        field = config_service.get_field(db, session.user.tenant_id, field_key)
    except CMSError as exc:
        return _render(request, "error.html", session, status_code=404, errors=_errors(exc))
    return _render(request, "field_edit.html", session, field=field)


@router.post("/config/fields/{field_key}", response_class=HTMLResponse)
def field_save(
    field_key: str,
    request: Request,
    csrf_token: str = Form(""),
    label: str = Form(...),
    description: str = Form(""),
    required: str | None = Form(None),
    enabled: str | None = Form(None),
    options: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    session = _guard(request, db, Permission.FIELDS_WRITE)
    _check_csrf(session, csrf_token)
    tenant_id = session.user.tenant_id
    try:
        field = config_service.get_field(db, tenant_id, field_key)
        changes = FieldUpdate(
            label=label,
            description=description,
            required=required is not None,
            enabled=enabled is not None,
            options=_lines(options) if field.data_type == DataType.ENUM else None,
        )
        _, changed, revision = config_service.update_field(db, tenant_id, field_key, changes)
    except (CMSError, ValidationError) as exc:
        db.rollback()
        field = config_service.get_field(db, tenant_id, field_key)
        return _render(
            request,
            "field_edit.html",
            session,
            status_code=422,
            field=field,
            errors=_errors(exc),
        )
    return _redirect("/config/fields", "saved" if changed else "unchanged", revision)


@router.get("/config/custom-fields/new", response_class=HTMLResponse)
def custom_field_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session = _guard(request, db, Permission.FIELDS_WRITE)
    return _render(
        request,
        "custom_field_new.html",
        session,
        entities=list(Entity),
        data_types=list(DataType),
        form={},
    )


@router.post("/config/custom-fields", response_class=HTMLResponse)
def custom_field_create(
    request: Request,
    csrf_token: str = Form(""),
    entity: str = Form(...),
    name: str = Form(...),
    label: str = Form(...),
    description: str = Form(""),
    data_type: str = Form(...),
    required: str | None = Form(None),
    options: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    session = _guard(request, db, Permission.FIELDS_WRITE)
    _check_csrf(session, csrf_token)
    form = {
        "entity": entity,
        "name": name,
        "label": label,
        "description": description,
        "data_type": data_type,
        "required": required is not None,
        "options": options,
    }
    try:
        payload = CustomFieldCreate.model_validate({**form, "options": _lines(options) or None})
        _, revision = config_service.create_custom_field(db, session.user.tenant_id, payload)
    except (CMSError, ValidationError) as exc:
        db.rollback()
        return _render(
            request,
            "custom_field_new.html",
            session,
            status_code=422,
            entities=list(Entity),
            data_types=list(DataType),
            form=form,
            errors=_errors(exc),
        )
    return _redirect("/config/fields", "saved", revision)


# --- intake mappings ----------------------------------------------------------------------------


def _mappings_context(db: Session, session: AuthSession) -> dict[str, Any]:
    tenant_id = session.user.tenant_id
    return {
        "mappings": config_service.list_mappings(db, tenant_id),
        "fields": [f for f in config_service.list_fields(db, tenant_id) if f.enabled],
        "transformations": list(Transformation),
        "revision": config_service.current_revision(db, tenant_id),
    }


@router.get("/config/mappings", response_class=HTMLResponse)
def mappings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session = _guard(request, db, Permission.CONFIG_READ)
    return _render(request, "mappings.html", session, form={}, **_mappings_context(db, session))


@router.post("/config/mappings", response_class=HTMLResponse)
def mapping_save(
    request: Request,
    csrf_token: str = Form(""),
    source_key: str = Form(...),
    target_field: str = Form(...),
    transformation: str = Form("none"),
    value_map: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    session = _guard(request, db, Permission.MAPPINGS_WRITE)
    _check_csrf(session, csrf_token)
    form = {
        "source_key": source_key,
        "target_field": target_field,
        "transformation": transformation,
        "value_map": value_map,
    }
    try:
        payload = MappingUpsert.model_validate(
            {
                "target_field": target_field,
                "transformation": transformation,
                "value_map": _parse_value_map(value_map),
            }
        )
        _, changed, revision = config_service.upsert_mapping(
            db, session.user.tenant_id, source_key.strip(), payload
        )
    except (CMSError, ValidationError) as exc:
        db.rollback()
        return _render(
            request,
            "mappings.html",
            session,
            status_code=422,
            form=form,
            errors=_errors(exc),
            **_mappings_context(db, session),
        )
    return _redirect("/config/mappings", "saved" if changed else "unchanged", revision)


@router.post("/config/mappings/{source_key}/delete", response_class=HTMLResponse)
def mapping_delete(
    source_key: str,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    session = _guard(request, db, Permission.MAPPINGS_WRITE)
    _check_csrf(session, csrf_token)
    try:
        revision = config_service.delete_mapping(db, session.user.tenant_id, source_key)
    except CMSError as exc:
        return _render(
            request,
            "mappings.html",
            session,
            status_code=exc.status_code,
            form={},
            errors=_errors(exc),
            **_mappings_context(db, session),
        )
    return _redirect("/config/mappings", "deleted", revision)


# --- intake form and records --------------------------------------------------------------------


@router.get("/intake/new", response_class=HTMLResponse)
def intake_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session = _guard(request, db, Permission.INTAKE_SUBMIT)
    return _render(
        request,
        "intake_new.html",
        session,
        prefix=_VALUE_PREFIX,
        submitted={},
        mappings=config_service.list_mappings(db, session.user.tenant_id),
    )


@router.post("/intake", response_class=HTMLResponse)
async def intake_submit(request: Request, db: Session = Depends(get_db)) -> Response:
    session = _guard(request, db, Permission.INTAKE_SUBMIT)
    form = await request.form()
    _check_csrf(session, str(form.get("csrf_token", "")))
    submitted = {
        k.removeprefix(_VALUE_PREFIX): str(v)
        for k, v in form.items()
        if k.startswith(_VALUE_PREFIX)
    }
    try:
        record = intake_service.submit_intake(db, session.user, dict(submitted))
    except CMSError as exc:
        return _render(
            request,
            "intake_new.html",
            session,
            status_code=exc.status_code,
            prefix=_VALUE_PREFIX,
            submitted=submitted,
            errors=_errors(exc),
            mappings=config_service.list_mappings(db, session.user.tenant_id),
        )
    return RedirectResponse(f"/records?created={record.id}", status_code=303)


@router.get("/records", response_class=HTMLResponse)
def records_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    session = _guard(request, db, Permission.RECORDS_READ)
    created = request.query_params.get("created", "")
    return _render(
        request,
        "records.html",
        session,
        records=intake_service.list_records(db, session.user.tenant_id),
        created=int(created) if created.isdigit() else None,
    )


def register_ui_error_handlers(app: FastAPI) -> None:
    async def handle_login_required(request: Request, exc: Exception) -> Response:
        return RedirectResponse("/login", status_code=303)

    async def handle_forbidden(request: Request, exc: Exception) -> Response:
        session = exc.session if isinstance(exc, PageForbidden) else None
        return _render(
            request,
            "error.html",
            session,
            status_code=403,
            errors=["You do not have permission to view or change this page."],
        )

    app.add_exception_handler(LoginRequired, handle_login_required)
    app.add_exception_handler(PageForbidden, handle_forbidden)
