from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    INTEGRATION = "integration"
    INTAKE_COORDINATOR = "intake_coordinator"
    STAFF = "staff"


class Permission(StrEnum):
    SCHEMA_READ = "schema:read"
    CONFIG_READ = "config:read"
    FIELDS_WRITE = "fields:write"
    MAPPINGS_WRITE = "mappings:write"
    INTAKE_SUBMIT = "intake:submit"
    RECORDS_READ = "records:read"


# The integration role can configure the CMS but cannot read or create client records:
# onboarding connectors get least privilege.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.INTEGRATION: frozenset(
        {
            Permission.SCHEMA_READ,
            Permission.CONFIG_READ,
            Permission.FIELDS_WRITE,
            Permission.MAPPINGS_WRITE,
        }
    ),
    Role.INTAKE_COORDINATOR: frozenset(
        {Permission.SCHEMA_READ, Permission.INTAKE_SUBMIT, Permission.RECORDS_READ}
    ),
    Role.STAFF: frozenset(
        {Permission.SCHEMA_READ, Permission.CONFIG_READ, Permission.RECORDS_READ}
    ),
}


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[Role(role)]
