"""Firm sign-up, the caller's own firm, its users and its CMS connectors."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require
from app.config import get_settings
from app.db.session import get_db
from app.domain import auth_service, project_service
from app.errors import Forbidden
from app.models import User
from app.permissions import Permission
from app.schemas.api import (
    ConnectorCreate,
    ConnectorOut,
    FirmOut,
    SignupRequest,
    UserCreate,
    UserOut,
)

router = APIRouter(prefix="/api/v1", tags=["firms"])


@router.post("/firms", response_model=UserOut, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> UserOut:
    """Create a new, isolated firm and its first firm admin (local demo self-service)."""
    if not get_settings().allow_signup:
        raise Forbidden("Firm sign-up is disabled.", [{"code": "signup_disabled"}])
    return UserOut.model_validate(auth_service.signup(db, body))


@router.get("/firm", response_model=FirmOut)
def current_firm(user: User = Depends(require(Permission.FIRM_READ))) -> FirmOut:
    return FirmOut.model_validate(user.firm)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    user: User = Depends(require(Permission.USERS_MANAGE)),
    db: Session = Depends(get_db),
) -> UserOut:
    return UserOut.model_validate(auth_service.create_user(db, user.firm_id, body))


@router.get("/users", response_model=list[UserOut])
def list_users(
    user: User = Depends(require(Permission.USERS_MANAGE)), db: Session = Depends(get_db)
) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in auth_service.list_users(db, user.firm_id)]


@router.post("/connectors", response_model=ConnectorOut, status_code=201)
def create_connector(
    body: ConnectorCreate,
    user: User = Depends(require(Permission.CONNECTORS_MANAGE)),
    db: Session = Depends(get_db),
) -> ConnectorOut:
    return ConnectorOut.model_validate(project_service.create_connector(db, user, body))


@router.get("/connectors", response_model=list[ConnectorOut])
def list_connectors(
    user: User = Depends(require(Permission.FIRM_READ)), db: Session = Depends(get_db)
) -> list[ConnectorOut]:
    return [ConnectorOut.model_validate(c) for c in project_service.list_connectors(db, user)]
