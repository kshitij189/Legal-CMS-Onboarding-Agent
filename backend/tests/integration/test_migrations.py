from alembic import command
from alembic.config import Config

from app.config import REPO_ROOT
from tests.conftest import TEST_DATABASE_URL


def test_models_match_migrations() -> None:
    """Fails if a model change has no migration (tables, columns, indexes, FKs)."""
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.check(config)
