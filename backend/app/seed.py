"""Seed the demo firms A/B/C with reviewer accounts and a mock CMS connector. Idempotent.

Usage: python -m app.seed
Demo logins: <a|b|c>.admin (firm_admin) and <a|b|c>.specialist (integration_specialist), password
ONBOARDING_SEED_PASSWORD (default "demo-password"). Synthetic data only.
"""

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.integrations.registry import SUPPORTED_OPERATIONS
from app.models import CMSConnector, Firm, User
from app.permissions import Role
from app.security import hash_password

DEMO_FIRMS = (
    ("a", "firm-a", "Alder & Associates (fictional)"),
    ("b", "firm-b", "Birch Legal LLP (fictional)"),
    ("c", "firm-c", "Cedar Law Group (fictional)"),
)


def seed(db: Session, password: str, cms_base_url: str) -> list[str]:
    created: list[str] = []
    password_hash = hash_password(password)
    for prefix, slug, name in DEMO_FIRMS:
        firm = db.scalar(select(Firm).where(Firm.slug == slug))
        if firm is None:
            firm = Firm(slug=slug, name=name)
            db.add(firm)
            db.flush()
            created.append(f"firm {slug}")
        for suffix, role in (
            ("admin", Role.FIRM_ADMIN),
            ("specialist", Role.INTEGRATION_SPECIALIST),
        ):
            username = f"{prefix}.{suffix}"
            if db.scalar(select(User).where(User.username == username)) is None:
                db.add(
                    User(
                        firm_id=firm.id,
                        username=username,
                        display_name=f"{name} {role.value.replace('_', ' ')}",
                        password_hash=password_hash,
                        role=role.value,
                    )
                )
                created.append(f"user {username}")
        connector = db.scalar(
            select(CMSConnector).where(
                CMSConnector.firm_id == firm.id, CMSConnector.name == "Mock CMS"
            )
        )
        if connector is None:
            db.add(
                CMSConnector(
                    firm_id=firm.id,
                    name="Mock CMS",
                    connector_type="mock_cms_api",
                    base_url=cms_base_url.rstrip("/"),
                    credential_ref=f"{slug}-mock-cms",
                    supported_operations=SUPPORTED_OPERATIONS["mock_cms_api"],
                )
            )
            created.append(f"connector {slug}/Mock CMS")
    db.commit()
    return created


def main() -> None:
    password = os.environ.get("ONBOARDING_SEED_PASSWORD", "demo-password")
    base_url = os.environ.get("ONBOARDING_SEED_CMS_BASE_URL", "http://localhost:8100")
    with get_sessionmaker()() as db:
        created = seed(db, password, base_url)
    print("\n".join(created) if created else "Nothing to seed; demo data already present.")


if __name__ == "__main__":
    main()
