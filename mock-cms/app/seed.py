"""Seed fictional firm workspaces and demo users. Idempotent: existing firms/users are left alone.

Usage: python -m app.seed
"""

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Tenant, User
from app.permissions import Role
from app.security import hash_password
from app.services.config_service import create_tenant

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "demo_firms.json"


def seed(db: Session, firms: list[dict[str, Any]], password: str) -> list[str]:
    created: list[str] = []
    for firm in firms:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == firm["slug"]))
        if tenant is None:
            tenant = create_tenant(db, firm["slug"], firm["name"])
            created.append(f"tenant {firm['slug']}")
        for user in firm["users"]:
            if db.scalar(select(User).where(User.username == user["username"])) is None:
                db.add(
                    User(
                        tenant_id=tenant.id,
                        username=user["username"],
                        display_name=user["display_name"],
                        role=Role(user["role"]).value,
                        password_hash=hash_password(password),
                    )
                )
                created.append(f"user {user['username']}")
    db.commit()
    return created


def main() -> None:
    password = os.environ.get("MOCK_CMS_SEED_PASSWORD", "demo-password")
    firms = json.loads(FIXTURE.read_text(encoding="utf-8"))["firms"]
    with get_sessionmaker()() as db:
        created = seed(db, firms, password)
    print("\n".join(created) if created else "Nothing to seed; demo data already present.")


if __name__ == "__main__":
    main()
