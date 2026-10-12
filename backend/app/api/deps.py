from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.auth_service import resolve_token
from app.errors import Forbidden, Unauthenticated
from app.models import AuthToken, User
from app.permissions import Permission, has_permission

_bearer = HTTPBearer(auto_error=False)


def current_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthToken:
    if credentials is None:
        raise Unauthenticated("Missing bearer token.")
    token = resolve_token(db, credentials.credentials)
    if token is None:
        raise Unauthenticated("Invalid or expired token.")
    return token


def require(permission: Permission) -> Callable[..., User]:
    def dependency(token: AuthToken = Depends(current_token)) -> User:
        if not has_permission(token.user.role, permission):
            raise Forbidden(
                f"Role '{token.user.role}' lacks permission '{permission.value}'.",
                [{"code": "missing_permission", "permission": permission.value}],
            )
        return token.user

    return dependency
