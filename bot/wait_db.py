"""Перед миграциями ждём, пока база станет доступна: python -m bot.wait_db.

Приватная сеть хостинга иногда поднимается через несколько секунд после старта контейнера, а неверная
строка подключения выглядит как «No address associated with hostname». Здесь мы ждём до WAIT_DB_SECONDS
и пишем в лог понятную причину: хост и порт, без логина и пароля.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from urllib.parse import urlsplit

from sqlalchemy import text

from bot.config import Settings
from bot.db.engine import make_engine

log = logging.getLogger("wait_db")


def describe(database_url: str) -> str:
    """host:port/db без логина и пароля, для логов."""
    parts = urlsplit(database_url)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def check_url(database_url: str) -> str | None:
    """Текст ошибки, если строка подключения явно битая, иначе None."""
    if not database_url:
        return "DATABASE_URL пуст"
    if "${{" in database_url or "}}" in database_url:
        return "DATABASE_URL содержит неразвёрнутую ссылку ${{...}}: проверьте имя сервиса базы в ссылке"
    parts = urlsplit(database_url)
    if parts.scheme not in ("postgresql", "postgres", "postgresql+asyncpg"):
        return f"DATABASE_URL должен начинаться с postgresql://, сейчас схема «{parts.scheme}»"
    if not parts.hostname:
        return "в DATABASE_URL нет хоста"
    return None


async def wait(database_url: str, root_cert: str, timeout: float) -> bool:
    engine = make_engine(database_url, root_cert)
    deadline = time.monotonic() + timeout
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                log.info("database %s is reachable (attempt %s)", describe(database_url), attempt)
                return True
            except Exception as e:  # noqa: BLE001
                if time.monotonic() >= deadline:
                    log.error("database %s is not reachable after %ss: %r", describe(database_url), int(timeout), e)
                    return False
                log.warning("database %s not ready (attempt %s): %r", describe(database_url), attempt, e)
                await asyncio.sleep(min(2 * attempt, 5))
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    problem = check_url(s.database_url)
    if problem:
        log.error(problem)
        return 1
    timeout = float(os.environ.get("WAIT_DB_SECONDS") or 60)
    return 0 if asyncio.run(wait(s.database_url, s.pg_ssl_root_cert, timeout)) else 1


if __name__ == "__main__":
    sys.exit(main())
