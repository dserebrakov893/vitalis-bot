"""Даты по-русски, имя для обращения, ссылки на календарь."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bot.config import MSK

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
EVENT_DURATION = timedelta(hours=3)  # если у встречи не указано время окончания


def event_end(event) -> datetime:
    return event.ends_at or event.starts_at + EVENT_DURATION


def msk(dt: datetime) -> datetime:
    return dt.astimezone(MSK)


def fmt_date(dt: datetime) -> str:
    """19 ноября"""
    d = msk(dt)
    return f"{d.day} {MONTHS[d.month - 1]}"


def fmt_time(dt: datetime) -> str:
    return msk(dt).strftime("%H:%M")


def fmt_datetime(dt: datetime) -> str:
    """19 ноября, 17:00"""
    return f"{fmt_date(dt)}, {fmt_time(dt)}"


def fmt_weekday(dt: datetime) -> str:
    return WEEKDAYS[msk(dt).weekday()]


def first_name(full_name: str | None, fallback: str | None = None) -> str:
    """ФИО вводят как в паспорте: «Фамилия Имя Отчество», обращаемся по имени."""
    if full_name:
        parts = full_name.split()
        if len(parts) >= 2:
            return parts[1]
        return parts[0]
    return fallback or "друг"


def _gcal_dates(starts_at: datetime, ends_at: datetime | None = None) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    start = starts_at.astimezone(timezone.utc)
    end = (ends_at or starts_at + EVENT_DURATION).astimezone(timezone.utc)
    return f"{start.strftime(fmt)}/{end.strftime(fmt)}"


def calendar_url(title: str, starts_at: datetime, location: str | None = None,
                 ends_at: datetime | None = None) -> str:
    """Ссылка Google Calendar для inline-кнопки (без ограничения длины)."""
    url = (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote('Vitalis · ' + title)}&dates={_gcal_dates(starts_at, ends_at)}"
    )
    if location:
        url += "&location=" + quote(location)
    return url


def calendar_url_short(starts_at: datetime, ends_at: datetime | None = None) -> str:
    """Для openLink в виджете: только https и не длиннее 256 символов, поэтому без кириллицы и адреса."""
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text=Vitalis&dates={_gcal_dates(starts_at, ends_at)}"
    )
