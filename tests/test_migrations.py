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
    assert ev.starts_at == datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
    assert ev.seats == 60 and ev.registration_limit == 75 and ev.is_open
    assert "Зубовский бульвар, д. 37, стр. 1" in ev.venue_text
    assert ev.venue_url.startswith("https://yandex.ru/maps/")
