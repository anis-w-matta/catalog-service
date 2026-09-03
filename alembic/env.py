import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.config import settings
from app.models import Base

# Optional: pin alembic_version to a specific schema (e.g. when migrating an
# isolated test schema that shares a search_path with `public`). Unset for
# normal runs - behaves exactly as before.
version_table_schema = os.environ.get("ALEMBIC_SCHEMA")

config = context.config
# set_main_option() applies BasicInterpolation to '%' on every subsequent
# read - a percent-encoded DATABASE_URL raises "invalid interpolation
# syntax" otherwise. Escaping '%' -> '%%' survives the round trip.
config.set_main_option("sqlalchemy.url",
                       settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=version_table_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            version_table_schema=version_table_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
