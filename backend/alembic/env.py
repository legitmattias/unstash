"""Alembic migration environment.

Connects to PostgreSQL as the ``unstash_migrations`` role (schema owner,
BYPASSRLS) using the same Pydantic settings the application uses. Application
runtime never goes through this path — it uses the ``unstash_app`` role.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from unstash.config import get_settings

# Alembic Config object — gives access to alembic.ini values.
config = context.config

# Set up loggers from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the migrations DSN built from application settings. The URL is held as a
# SQLAlchemy URL object rather than written to alembic.ini, so secret values
# never touch disk and percent-encoded passwords don't trip configparser
# interpolation.
settings = get_settings()
migrations_url = settings.database_migrations_url

# Target metadata for autogenerate. Wired up when the first model lands.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL statements to the script output without connecting to the
    database. Useful for generating migration scripts to apply manually.
    """
    context.configure(
        url=migrations_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
    connectable = create_engine(migrations_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
