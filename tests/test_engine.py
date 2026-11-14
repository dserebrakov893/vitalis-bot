"""Подключение: только DATABASE_URL, SSL опционален."""
import ssl
import tempfile

import pytest

from bot.db.engine import build_async_url, build_ssl


@pytest.mark.parametrize(
    "url, expected, mode",
    [
        ("postgresql://u:p@postgres.railway.internal:5432/railway", "postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway", None),
        ("postgres://u:p@host:5432/db?sslmode=require", "postgresql+asyncpg://u:p@host:5432/db", "require"),
        ("postgresql://u:p@host:6432/db?sslmode=verify-full&application_name=bot", "postgresql+asyncpg://u:p@host:6432/db?application_name=bot", "verify-full"),
    ],
)
def test_url(url, expected, mode):
    assert build_async_url(url) == (expected, mode)


def test_ssl_without_cert_uses_url_mode():
    assert build_ssl("", None) is None  # asyncpg по умолчанию: prefer
    assert build_ssl("", "disable") == "disable"
    assert build_ssl("", "require") == "require"


def test_ssl_with_cert_is_verify_full():
    # любой валидный PEM подойдёт: берём системный набор CA из Python
    cafile = ssl.get_default_verify_paths().cafile or ssl.get_default_verify_paths().openssl_cafile
    with open(cafile, "rb") as src, tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as dst:
        dst.write(src.read())
    ctx = build_ssl(dst.name, "disable")  # сертификат важнее режима из URL
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
