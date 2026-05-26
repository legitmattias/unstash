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
from unstash.db.models import Base

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

# Target metadata for autogenerate. The models package imports every model
# as a side effect, registering them with Base.metadata.
target_metadata = Base.metadata

# Specialised indexes created by hand in migrations via raw SQL — their DDL
# (pgvectorscale diskann access method, pg_search bm25 tokenizer casts) has no
# SQLAlchemy expression, so they are absent from the model metadata.
# Autogenerate would therefore see them as "extra" and emit drops. Exclude
# them by name so future autogenerate runs leave them alone.
_UNMANAGED_INDEXES = frozenset(
    {
        "ix_chunks_embedding_diskann",
        "ix_chunks_text_bm25",
    }
)


def _include_object(
    obj: object,  # noqa: ARG001 — required by Alembic's callback signature
    name: str | None,
    type_: str,
    reflected: bool,  # noqa: ARG001
    compare_to: object,  # noqa: ARG001
) -> bool:
    """Tell autogenerate to ignore the hand-maintained specialised indexes."""
    if type_ == "index" and name in _UNMANAGED_INDEXES:
        return False
    return True


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
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
    connectable = create_engine(migrations_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
