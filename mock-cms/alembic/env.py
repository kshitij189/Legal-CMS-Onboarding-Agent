from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401 - registers tables on Base.metadata
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    # Keep loggers created before migrations (the app's) enabled when migrating in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Tests pass an explicit URL through the Alembic config; otherwise use app settings.
url = config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
