"""Настройки из переменных окружения. Пустые [TODO]-значения допустимы."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
BOT_USERNAME = "vitalis_business_club_bot"
OPERATOR_EMAIL = "hello@justbusinessdarling.com"
JBD_SITE = "justbusinessdarling.com"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _https_url(value: str | None) -> str:
    """https://хост[/путь] без завершающего слеша. Битое значение (например, «https://» от пустой
    ссылки ${{RAILWAY_PUBLIC_DOMAIN}}) считаем незаданным: бот подскажет, что поправить."""
    from urllib.parse import urlsplit

    value = (value or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
        return ""
    return value


def _ids(value: str | None) -> frozenset[int]:
    if not value:
        return frozenset()
    return frozenset(int(x) for x in value.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    database_url: str = ""
    pg_ssl_root_cert: str = ""
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    ticket_hmac_secret: str = ""
    privacy_policy_url: str = ""
    club_channel_url: str = ""
    club_chat_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    widgets_enabled: bool = True
    support_username: str = "meine_nika"
    support_chat_id: int | None = None
    webapp_url: str = ""  # публичный https-адрес сервиса, на нём открывается Mini App чекина
    webapp_url_raw: str = ""  # как задано в env: чтобы показать ошибку, если адрес битый
    port: int | None = None  # порт веб-сервера; без него Mini App не запускается

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            bot_token=e("BOT_TOKEN", ""),
            database_url=e("DATABASE_URL", ""),
            pg_ssl_root_cert=e("PG_SSL_ROOT_CERT", ""),
            admin_ids=_ids(e("ADMIN_IDS")),
            ticket_hmac_secret=e("TICKET_HMAC_SECRET", ""),
            privacy_policy_url=e("PRIVACY_POLICY_URL", ""),
            club_channel_url=e("CLUB_CHANNEL_URL", ""),
            club_chat_url=e("CLUB_CHAT_URL", ""),
            smtp_host=e("SMTP_HOST", ""),
            smtp_port=int(e("SMTP_PORT") or 587),
            smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""),
            smtp_from=e("SMTP_FROM", ""),
            widgets_enabled=_bool(e("WIDGETS_ENABLED"), True),
            # Куда падают вопросы из «Поддержки». Аккаунт должен хотя бы раз нажать /start в боте.
            support_username=(e("SUPPORT_USERNAME") or "meine_nika").lstrip("@"),
            support_chat_id=int(e("SUPPORT_CHAT_ID")) if (e("SUPPORT_CHAT_ID") or "").strip() else None,
            webapp_url=_https_url(e("WEBAPP_URL")),
            webapp_url_raw=(e("WEBAPP_URL") or "").strip(),
            port=int(e("PORT")) if (e("PORT") or "").strip() else None,
        )

    def validate_for_run(self) -> None:
        missing = [n for n, v in (("BOT_TOKEN", self.bot_token), ("DATABASE_URL", self.database_url)) if not v]
        if len(self.ticket_hmac_secret.encode()) < 32:
            missing.append("TICKET_HMAC_SECRET (32+ байт)")
        if missing:
            raise RuntimeError("Не заданы переменные окружения: " + ", ".join(missing))


settings = Settings.from_env()
