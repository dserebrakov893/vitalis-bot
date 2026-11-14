"""Подключение к PostgreSQL. Провайдер не важен: всё задаётся строкой DATABASE_URL.

SSL опционален: если задан PG_SSL_ROOT_CERT, подключаемся с проверкой как sslmode=verify-full по этому
сертификату. Если не задан, работает sslmode из DATABASE_URL (по умолчанию asyncpg: prefer).
"""
from __future__ import annotations

import ssl
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
# Все таблицы бота живут в своей схеме: база может быть общей с другими сервисами,
# а pg_dump -n vitalis переносит ровно данные бота.
SCHEMA = "vitalis"


def build_async_url(database_url: str) -> tuple[str, str | None]:
    """postgresql:// или postgres:// → postgresql+asyncpg://... без sslmode (его передаём отдельно).

    Возвращает (url, sslmode)."""
    parts = urlsplit(database_url)
    scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), sslmode


def build_ssl(root_cert: str, sslmode: str | None) -> ssl.SSLContext | str | None:
    """Что передать в asyncpg как ssl: контекст verify-full, режим из URL или None (по умолчанию)."""
    if root_cert:
        cert_path = Path(root_cert)
        if not cert_path.is_absolute():
            cert_path = ROOT / cert_path
        ctx = ssl.create_default_context(cafile=str(cert_path))
        # verify-full: проверяем и цепочку, и имя хоста
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    return sslmode  # disable / prefer / require / verify-ca / verify-full (системные CA) или None


def make_engine(database_url: str, root_cert: str = "") -> AsyncEngine:
    url, sslmode = build_async_url(database_url)
    connect_args: dict = {
        # За многими managed-провайдерами стоит пулер (pgbouncer/Odyssey). В режиме transaction
        # кэш подготовленных выражений asyncpg ломается, поэтому выключаем его на любом провайдере.
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        "server_settings": {"timezone": "UTC", "search_path": SCHEMA},
    }
    ssl_arg = build_ssl(root_cert, sslmode)
    if ssl_arg is not None:
        connect_args["ssl"] = ssl_arg
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
