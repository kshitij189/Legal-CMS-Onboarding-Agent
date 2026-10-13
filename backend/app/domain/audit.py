from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def record(
    db: Session,
    *,
    firm_id: int,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    project_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the caller's transaction. Never put secrets or client data in it."""
    db.add(
        AuditEvent(
            firm_id=firm_id,
            project_id=project_id,
            actor_user_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def list_events(db: Session, firm_id: int, project_id: int) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.firm_id == firm_id, AuditEvent.project_id == project_id)
            .order_by(AuditEvent.id)
        )
    )
