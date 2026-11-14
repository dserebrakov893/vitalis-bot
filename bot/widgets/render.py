"""Сборка HTML виджетов из шаблонов и их состояния (chat_data / public_data)."""
from __future__ import annotations

import json
import time
import zlib
from functools import lru_cache
from html import escape
from pathlib import Path

from bot.db.models import Event, Registration, User
from bot.formatting import calendar_url_short, fmt_datetime, fmt_weekday

TEMPLATES = Path(__file__).parent / "templates"
MAX_HTML = 32768
MAX_COMPRESSED = 4096
MAX_STATE = 1024
TICKET_STATUSES = ("registered", "soon", "live", "done", "cancelled")


class WidgetTooLarge(ValueError):
    pass


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    raw = (TEMPLATES / name).read_text(encoding="utf-8")
    # убираем переводы строк и отступы между тегами: меньше байт, тот же результат
    return "".join(line.strip() for line in raw.splitlines())


def compressed_size(html: str) -> int:
    """Так считает сервер Telegram: zlib, уровень 9."""
    return len(zlib.compress(html.encode("utf-8"), 9))


def check_size(html: str) -> str:
    raw = len(html.encode("utf-8"))
    packed = compressed_size(html)
    if raw > MAX_HTML or packed > MAX_COMPRESSED or "\x00" in html:
        raise WidgetTooLarge(f"widget html {raw} bytes, {packed} compressed")
    return html


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _when(event: Event) -> str:
    return f"{fmt_weekday(event.starts_at).capitalize()}, {fmt_datetime(event.starts_at)} (МСК)"


def event_card_html(event: Event) -> str:
    return check_size(
        _fill(
            _template("event_card.html"),
            {
                "title": escape(event.title),
                "when": escape(_when(event)),
                "venue": escape(event.venue_text or ""),
                "speaker": escape(event.speaker),
                "ts": str(int(event.starts_at.timestamp() * 1000)),
                "eid": str(event.id),
            },
        )
    )


def ticket_html(event: Event) -> str:
    """HTML билета общий для всех участников встречи; имя и номер приходят в chat_data."""
    cal = calendar_url_short(event.starts_at)
    if len(cal) > 256 or not cal.startswith("https://"):
        raise ValueError("calendar url is not acceptable for openLink")
    return check_size(
        _fill(
            _template("ticket.html"),
            {
                "when": escape(_when(event)),
                "venue": escape(event.venue_text or ""),
                "speaker": escape(event.speaker),
                "cal": json.dumps(cal),
            },
        )
    )


def revision() -> int:
    """Монотонная ревизия состояния: виджет игнорирует более старые данные."""
    return int(time.time() * 1000)


def _state(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_STATE:
        raise WidgetTooLarge("widget state over 1024 bytes")
    return text


def card_public_data(left: int, is_open: bool) -> str:
    return _state({"r": revision(), "l": left, "o": 1 if is_open else 0})


def ticket_chat_data(user: User, reg: Registration, status: str) -> str:
    assert status in TICKET_STATUSES
    data = {"r": revision(), "n": (user.full_name or user.tg_first_name or "")[:100], "no": reg.ticket_no, "s": status}
    if reg.feedback_score:
        data["sc"] = reg.feedback_score
    return _state(data)


def card_caption(event: Event, left: int) -> str:
    seats = f"Осталось мест: {left}" if left > 0 else "Мест нет, запишем в лист ожидания"
    return (
        f"Vitalis · {event.title}, {fmt_datetime(event.starts_at)}. {seats}.\n\n"
        "Не видишь карточку? Обнови Telegram или нажми /reg"
    )


def ticket_caption(event: Event, reg: Registration) -> str:
    return (
        f"Твой билет · Vitalis, {fmt_datetime(event.starts_at)}\n\n"
        "Не видишь карточку? Обнови Telegram или нажми /reg"
    )
