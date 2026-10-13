# The e2e tests use the same backend database fixtures as the integration tests.
from tests.integration.conftest import clean_tables, client, db, migrated_database  # noqa: F401
