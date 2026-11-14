"""Тесты гоняем на настоящем PostgreSQL (pgserver поднимает локальный кластер)."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text

os.environ.setdefault("TICKET_HMAC_SECRET", "test-secret-test-secret-test-secret-123")

from bot.db.engine import make_engine, make_sessionmaker  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "bot/db/migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def _in_thread(fn, *args):
    """alembic env.py делает asyncio.run, поэтому запускаем его вне цикла pytest-asyncio."""
    err: list[BaseException] = []

    def run():
        try:
            fn(*args)
        except BaseException as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if err:
        raise err[0]


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    import pgserver

    srv = pgserver.get_server(tempfile.mkdtemp(prefix="vitalis-pg-"), cleanup_mode="stop")
    srv.psql("DROP DATABASE IF EXISTS vitalis_test;")
    srv.psql("CREATE DATABASE vitalis_test;")
    yield srv.get_uri("vitalis_test")


@pytest.fixture(scope="session")
def migrated(pg_url):
    cfg = _alembic_cfg(pg_url)
    _in_thread(command.downgrade, cfg, "base")
    _in_thread(command.upgrade, cfg, "head")
    return pg_url


@pytest_asyncio.fixture(scope="session")
async def engine(migrated):
    eng = make_engine(migrated)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def sessionmaker(engine):
    return make_sessionmaker(engine)


@pytest_asyncio.fixture(autouse=True)
async def clean_db(request):
    if "engine" not in request.fixturenames:
        yield
        return
    engine = request.getfixturevalue("engine")
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE registrations, consents, users, fsm_state, deletion_log RESTART IDENTITY CASCADE"))
        await conn.execute(text("DELETE FROM events WHERE id <> 1"))
        await conn.execute(
            text(
                "UPDATE events SET seats=60, registration_limit=75, is_open=true, shared_widget_id=NULL, "
                "starts_at='2026-11-19 14:00+00' WHERE id=1"
            )
        )
    yield
