"""Подключение к PostgreSQL (Yandex Managed PostgreSQL, порт 6432, sslmode=verify-full)."""
from __future__ import annotations

import ssl
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]


def build_async_url(database_url: str) -> tuple[str, str | None]:
    """postgresql://... → postgresql+asyncpg://... без sslmode (asyncpg его не понимает).

    Возвращает (url, sslmode)."""
    parts = urlsplit(database_url)
    scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), sslmode


def build_ssl_context(root_cert: str, sslmode: str | None) -> ssl.SSLContext | None:
    if sslmode == "disable" or (not root_cert and sslmode is None):
        return None
    cert_path = Path(root_cert)
    if not cert_path.is_absolute():
        cert_path = ROOT / cert_path
    ctx = ssl.create_default_context(cafile=str(cert_path))
    # verify-full: проверяем и цепочку, и имя хоста
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def make_engine(database_url: str, root_cert: str = "") -> AsyncEngine:
    url, sslmode = build_async_url(database_url)
    connect_args: dict = {
        # Yandex ставит перед кластером Odyssey (пулер). В режиме transaction
        # кэш подготовленных выражений asyncpg ломается, поэтому выключаем его.
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        "server_settings": {"timezone": "UTC"},
    }
    ctx = build_ssl_context(root_cert, sslmode)
    if ctx is not None:
        connect_args["ssl"] = ctx
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
