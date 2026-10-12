from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthSession, User, utcnow
from app.security import hash_token, new_token, verify_password


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: Session, user: User, kind: str) -> tuple[str, AuthSession]:
    token = new_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=new_token(),
        kind=kind,
        expires_at=utcnow() + timedelta(minutes=get_settings().session_ttl_minutes),
    )
    db.add(session)
    db.commit()
    return token, session


def resolve_session(db: Session, token: str, kind: str) -> AuthSession | None:
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token), AuthSession.kind == kind
        )
    )
    if session is None or session.expires_at <= utcnow() or not session.user.is_active:
        return None
    return session


def revoke_session(db: Session, session_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.id == session_id))
    db.commit()
