from bot.wait_db import check_url, describe, wait


def test_check_url():
    assert check_url("") == "DATABASE_URL пуст"
    assert "неразвёрнутую" in check_url("${{Postgres.DATABASE_URL}}")
    assert "схема" in check_url("mysql://u:p@h/db")
    assert check_url("postgresql://u:p@postgres.railway.internal:5432/railway") is None


def test_describe_hides_credentials():
    assert describe("postgresql://user:secret@host.internal:5432/railway") == "host.internal:5432/railway"


async def test_wait_ok_and_timeout(migrated):
    assert await wait(migrated, "", timeout=5)
    assert not await wait("postgresql://u:p@no-such-host.invalid:5432/db", "", timeout=0)
