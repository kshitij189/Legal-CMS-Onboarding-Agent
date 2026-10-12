"""Reviewer accounts and bearer tokens (decision D5)."""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Conflict
from app.models import AuthToken, Firm, User, utcnow
from app.permissions import Role
from app.schemas.api import SignupRequest, UserCreate
from app.security import hash_password, hash_token, new_token, verify_password


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active or not user.firm.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def issue_token(db: Session, user: User) -> tuple[str, AuthToken]:
    token = new_token()
    record = AuthToken(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(minutes=get_settings().token_ttl_minutes),
    )
    db.add(record)
    db.commit()
    return token, record


def resolve_token(db: Session, token: str) -> AuthToken | None:
    record = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if (
        record is None
        or record.expires_at <= utcnow()
        or not record.user.is_active
        or not record.user.firm.is_active
    ):
        return None
    return record


def revoke_token(db: Session, token_id: int) -> None:
    db.execute(delete(AuthToken).where(AuthToken.id == token_id))
    db.commit()


def signup(db: Session, payload: SignupRequest) -> User:
    """Create a firm together with its first firm admin, atomically."""
    firm = Firm(slug=payload.firm.slug, name=payload.firm.name, timezone=payload.firm.timezone)
    db.add(firm)
    try:
        db.flush()
        admin = _new_user(firm.id, payload.admin, Role.FIRM_ADMIN)
        db.add(admin)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("Firm slug or admin username is already taken.") from None
    return admin


def create_user(db: Session, firm_id: int, payload: UserCreate) -> User:
    user = _new_user(firm_id, payload, payload.role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"Username '{payload.username}' is already taken.") from None
    return user


def list_users(db: Session, firm_id: int) -> list[User]:
    return list(db.scalars(select(User).where(User.firm_id == firm_id).order_by(User.id)))


def _new_user(firm_id: int, payload: UserCreate | SignupRequest.Admin, role: Role) -> User:
    return User(
        firm_id=firm_id,
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=role.value,
    )
