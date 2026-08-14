"""Alembic environment for the FinOpsAI warehouse.

Two guardrails live here:

* the database URL comes from application settings, never from alembic.ini;
* autogenerate and the version table are pinned to the ``finops`` schema, so
  Alembic can never touch the Prisma-managed LiteLLM tables that share the
  server.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, MetaData, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from finopsai.config import get_settings

SCHEMA = "finops"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Domain models land here in the schema phase; None keeps autogenerate inert.
target_metadata: MetaData | None = None


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Restrict autogenerate to the finops schema, ignoring LiteLLM tables."""
    if type_ == "table":
        return bool(getattr(obj, "schema", None) == SCHEMA)
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        compare_type=True,
    )


def do_run_migrations(connection: Connection) -> None:
    """Ensure the schema exists, then run migrations inside one transaction."""
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
