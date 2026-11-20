from datetime import datetime, timezone

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from bot.db.models import Base, Event


async def test_migrations_match_models(engine):
    def diff(conn):
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        changes = await conn.run_sync(diff)
    # alembic_version живёт только в БД, это не расхождение
    changes = [c for c in changes if not (c[0] == "remove_table" and c[1].name == "alembic_version")]
    assert changes == []


async def test_seed_first_event(sessionmaker):
    async with sessionmaker() as s:
        ev = (await s.execute(select(Event).where(Event.id == 1))).scalar_one()
    # год фикстура сдвигает в будущее, чтобы тесты не зависели от сегодняшней даты
    assert (ev.starts_at.month, ev.starts_at.day, ev.starts_at.hour) == (11, 19, 14) and ev.starts_at.utcoffset() == timezone.utc.utcoffset(None)
    assert ev.seats == 60 and ev.registration_limit == 75 and ev.is_open
    assert "Зубовский бульвар, д. 37, стр. 1" in ev.venue_text
    assert ev.venue_url.startswith("https://yandex.ru/maps/")


async def test_migrations_coexist_with_foreign_tables(migrated):
    """База общая: в public уже есть чужая таблица users. Бот её не трогает и живёт в схеме vitalis."""
    import asyncpg
    from alembic import command

    from bot.db.engine import SCHEMA
    from tests.conftest import _alembic_cfg, _in_thread

    shared = migrated.replace("/vitalis_test", "/vitalis_shared")
    sys_conn = await asyncpg.connect(migrated)
    await sys_conn.execute("DROP DATABASE IF EXISTS vitalis_shared")
    await sys_conn.execute("CREATE DATABASE vitalis_shared")
    await sys_conn.close()

    conn = await asyncpg.connect(shared)
    await conn.execute("CREATE TABLE public.users (id serial PRIMARY KEY, nickname text)")
    await conn.execute("INSERT INTO public.users (nickname) VALUES ('чужой')")
    await conn.close()

    _in_thread(command.upgrade, _alembic_cfg(shared), "head")
    _in_thread(command.upgrade, _alembic_cfg(shared), "head")  # повторный старт ничего не ломает

    conn = await asyncpg.connect(shared)
    try:
        assert await conn.fetchval("SELECT nickname FROM public.users") == "чужой"
        tables = {r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = $1", SCHEMA)}
        assert {"users", "events", "registrations", "consents", "fsm_state", "alembic_version"} <= tables
        assert await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.events") == 1
    finally:
        await conn.close()
