from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import Forbidden, Unauthenticated
from app.models import AuthSession, User
from app.permissions import Permission, has_permission
from app.services.auth_service import resolve_session

_bearer = HTTPBearer(auto_error=False)


def api_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthSession:
    if credentials is None:
        raise Unauthenticated("Missing bearer token.")
    session = resolve_session(db, credentials.credentials, kind="api")
    if session is None:
        raise Unauthenticated("Invalid or expired token.")
    return session


def require(permission: Permission) -> Callable[..., User]:
    def dependency(session: AuthSession = Depends(api_session)) -> User:
        if not has_permission(session.user.role, permission):
            raise Forbidden(
                f"Role '{session.user.role}' lacks permission '{permission.value}'.",
                [{"code": "missing_permission", "permission": permission.value}],
            )
        return session.user

    return dependency
