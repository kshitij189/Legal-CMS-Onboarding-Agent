import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog import BUILTIN_FIELDS
from app.models import FieldDefinition, Tenant, User
from app.seed import FIXTURE, seed
from app.services.auth_service import authenticate


def test_seed_creates_demo_firms_and_is_idempotent(db: Session) -> None:
    firms = json.loads(FIXTURE.read_text(encoding="utf-8"))["firms"]
    first = seed(db, firms, "pw")
    assert "tenant firm-c" in first
    assert "user c.integration" in first
    # firm-a/firm-b already exist from the test fixture; their tenants are reused.
    assert "tenant firm-a" not in first
    assert seed(db, firms, "pw") == []

    firm_c = db.scalar(select(Tenant).where(Tenant.slug == "firm-c"))
    assert firm_c is not None
    field_count = db.scalar(
        select(func.count())
        .select_from(FieldDefinition)
        .where(FieldDefinition.tenant_id == firm_c.id)
    )
    assert field_count == len(BUILTIN_FIELDS)
    roles = set(db.scalars(select(User.role).where(User.tenant_id == firm_c.id)))
    assert roles == {"admin", "integration", "intake_coordinator", "staff"}
    assert authenticate(db, "c.admin", "pw") is not None
