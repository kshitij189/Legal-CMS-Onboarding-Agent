"""Reviewer roles (decision D5). Every user belongs to exactly one firm and only sees that firm."""

from enum import StrEnum


class Role(StrEnum):
    # Configures the firm: users, CMS connectors, projects, intake schemas (blueprint §3).
    FIRM_ADMIN = "firm_admin"
    # Works the onboarding: schemas, target inspection; approves mappings from Phase 3 on.
    INTEGRATION_SPECIALIST = "integration_specialist"


class Permission(StrEnum):
    FIRM_READ = "firm:read"
    USERS_MANAGE = "users:manage"
    CONNECTORS_MANAGE = "connectors:manage"
    PROJECTS_CREATE = "projects:create"
    PROJECTS_READ = "projects:read"
    SOURCE_SCHEMA_WRITE = "source_schema:write"
    TARGET_INSPECT = "target:inspect"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.FIRM_ADMIN: frozenset(Permission),
    Role.INTEGRATION_SPECIALIST: frozenset(
        {
            Permission.FIRM_READ,
            Permission.PROJECTS_READ,
            Permission.SOURCE_SCHEMA_WRITE,
            Permission.TARGET_INSPECT,
        }
    ),
}


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[Role(role)]
