"""Alembic env: асинхронный движок, тот же, что у бота (SSL verify-full)."""
import asyncio
import os

from alembic import context

from bot.config import Settings
from sqlalchemy import text

from bot.db.engine import SCHEMA, make_engine
from bot.db.models import Base

target_metadata = Base.metadata


def _url_and_cert() -> tuple[str, str]:
    # В тестах URL передаётся через config.attributes, в бою из env
    url = context.config.attributes.get("database_url")
    if url:
        return url, context.config.attributes.get("root_cert", "")
    s = Settings.from_env()
    if not s.database_url:
        raise RuntimeError("DATABASE_URL не задан")
    return s.database_url, s.pg_ssl_root_cert


def _do_run(connection) -> None:
    # Своя схема: не задеваем чужие таблицы в public, если база общая
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()
    if connection.in_transaction():
        connection.commit()


async def _run_async() -> None:
    url, cert = _url_and_cert()
    engine = make_engine(url, cert)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


def run_migrations_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:
        _do_run(conn)
    else:
        asyncio.run(_run_async())


if context.is_offline_mode():
    raise RuntimeError("Офлайн-режим не поддерживается")
run_migrations_online()
