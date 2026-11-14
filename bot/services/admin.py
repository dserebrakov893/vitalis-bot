"""Данные для админки: статистика, CSV-выгрузка, список для охраны, параметры встречи."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import texts as t
from bot.config import MSK
from bot.db.models import Event, Registration, User
from bot.services import registration as regs

STATUS_LABELS = dict(t.STATUSES)
INTEREST_LABELS = dict(t.INTERESTS)
PROJECT_LABELS = dict(t.PROJECT_STAGES)
EDITABLE_FIELDS = ("venue_text", "venue_url", "is_open", "registration_limit", "starts_at")


async def admin_event(session: AsyncSession, event_id: int | None = None) -> Event | None:
    """Встреча для админ-команд: указанная, ближайшая открытая или последняя прошедшая."""
    if event_id is not None:
        return await session.get(Event, event_id)
    ev = await regs.get_current_event(session)
    if ev is not None:
        return ev
    return (await session.execute(select(Event).order_by(Event.starts_at.desc()).limit(1))).scalar_one_or_none()


def _top(counter: Counter, labels: dict | None = None, limit: int = 15) -> str:
    if not counter:
        return "  —"
    rows = []
    for key, n in counter.most_common(limit):
        name = (labels or {}).get(key, key) if key else "без метки"
        rows.append(f"  {name}: {n}")
    return "\n".join(rows)


async def stats_text(sm: async_sessionmaker, event_id: int | None = None) -> str:
    async with sm() as s:
        ev = await admin_event(s, event_id)
        if ev is None:
            return "Встреч нет."
        rows = (
            await s.execute(
                select(Registration, User).join(User).where(Registration.event_id == ev.id)
            )
        ).all()
    states = Counter(r.state for r, _ in rows)
    active = [(r, u) for r, u in rows if r.state in ("registered", "waitlist")]
    sources = Counter(r.source for r, _ in active)
    unis = Counter(u.university for _, u in active)
    statuses = Counter(u.status for _, u in active)
    interests = Counter(i for _, u in active for i in (u.interests or []))
    projects = Counter(u.project_stage for _, u in active)
    news = sum(1 for _, u in active if u.newsletter_opt_in)
    minors = sum(1 for _, u in active if u.age_group == "17_with_parent")
    scores = [r.feedback_score for r, _ in rows if r.feedback_score]
    lines = [
        f"<b>{ev.title}</b> (id {ev.id}), {ev.starts_at.astimezone(MSK):%d.%m %H:%M} МСК",
        f"Зарегистрировано: {states['registered']} из {ev.registration_limit} (мест в зале {ev.seats})",
        f"Лист ожидания: {states['waitlist']}",
        f"Отмены: {states['cancelled']}",
        f"Согласие на рассылку: {news} из {len(active)}",
        f"17 лет с согласия родителя: {minors}",
    ]
    if scores:
        lines.append(f"Оценка встречи: {sum(scores) / len(scores):.1f} ({len(scores)} оценок)")
    lines += [
        "\n<b>Источники</b>", _top(sources),
        "\n<b>Вузы</b>", _top(unis),
        "\n<b>Статусы</b>", _top(statuses, STATUS_LABELS),
        "\n<b>Интересы</b>", _top(interests, INTEREST_LABELS),
        "\n<b>Свой проект</b>", _top(projects, PROJECT_LABELS),
    ]
    from html import escape

    # экранируем всё, кроме наших тегов <b>
    return "\n".join(escape(x).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>") for x in lines)


EXPORT_COLUMNS = [
    ("event_id", lambda r, u: r.event_id),
    ("ticket_no", lambda r, u: r.ticket_no or ""),
    ("state", lambda r, u: r.state),
    ("full_name", lambda r, u: u.full_name or ""),
    ("phone", lambda r, u: u.phone or ""),
    ("email", lambda r, u: u.email or ""),
    ("university", lambda r, u: u.university or ""),
    ("status", lambda r, u: u.status or ""),
    ("interests", lambda r, u: ",".join(u.interests or [])),
    ("project_stage", lambda r, u: u.project_stage or ""),
    ("age_group", lambda r, u: u.age_group or ""),
    ("newsletter_opt_in", lambda r, u: "да" if u.newsletter_opt_in else "нет"),
    ("source", lambda r, u: r.source or ""),
    ("first_source", lambda r, u: u.source or ""),
    ("telegram_id", lambda r, u: u.id),
    ("username", lambda r, u: u.username or ""),
    ("registered_at_msk", lambda r, u: _msk(r.created_at)),
    ("waitlisted_at_msk", lambda r, u: _msk(r.waitlisted_at)),
    ("checked_in_at_msk", lambda r, u: _msk(r.checked_in_at)),
    ("feedback_score", lambda r, u: r.feedback_score or ""),
    ("reminded_24h", lambda r, u: int(r.reminded_24h)),
    ("reminded_2h", lambda r, u: int(r.reminded_2h)),
    ("feedback_sent", lambda r, u: int(r.feedback_sent)),
]


def _msk(dt: datetime | None) -> str:
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M") if dt else ""


def _safe_cell(value) -> str:
    """Защита от CSV-инъекций в Excel: значения, начинающиеся с = + - @, экранируем апострофом."""
    text = str(value)
    if text and text[0] in "=+-@\t\r" and not text.lstrip("+-").isdigit():
        return "'" + text
    return text


async def export_csv(sm: async_sessionmaker, event_id: int | None = None, all_events: bool = False) -> tuple[bytes, str]:
    async with sm() as s:
        q = select(Registration, User).join(User)
        name = "vitalis_all"
        if not all_events:
            ev = await admin_event(s, event_id)
            if ev is None:
                return b"", ""
            q = q.where(Registration.event_id == ev.id)
            name = f"vitalis_event{ev.id}"
        rows = (await s.execute(q.order_by(Registration.event_id, Registration.state, Registration.ticket_no))).all()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow([c for c, _ in EXPORT_COLUMNS])
    for r, u in rows:
        writer.writerow([_safe_cell(fn(r, u)) for _, fn in EXPORT_COLUMNS])
    return buf.getvalue().encode("utf-8-sig"), f"{name}_{datetime.now(MSK):%Y%m%d_%H%M}.csv"


async def guard_list(sm: async_sessionmaker, event_id: int | None = None) -> tuple[str, list[str]]:
    """Список для охраны: ФИО по алфавиту + номер билета, только registered."""
    async with sm() as s:
        ev = await admin_event(s, event_id)
        if ev is None:
            return "", []
        rows = (
            await s.execute(
                select(User.full_name, Registration.ticket_no)
                .join(User)
                .where(Registration.event_id == ev.id, Registration.state == "registered")
            )
        ).all()
    rows = sorted(rows, key=lambda r: ((r[0] or "").replace("ё", "е").replace("Ё", "Е").lower(), r[1]))
    from html import escape

    header = f"{escape(ev.title)}, {ev.starts_at.astimezone(MSK):%d.%m.%Y %H:%M} МСК. Всего: {len(rows)}"
    return header, [f"{i}. {escape(name or '')} — билет №{no}" for i, (name, no) in enumerate(rows, 1)]


def chunk_lines(header: str, lines: list[str], limit: int = 3800) -> list[str]:
    chunks, cur = [], header
    for line in lines:
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur += "\n" + line
    chunks.append(cur)
    return chunks


async def event_info(sm: async_sessionmaker, event_id: int | None = None) -> str:
    async with sm() as s:
        ev = await admin_event(s, event_id)
        if ev is None:
            return "Встреч нет."
        registered = await regs.count_registered(s, ev.id)
        waitlist = (
            await s.execute(
                select(func.count()).where(Registration.event_id == ev.id, Registration.state == "waitlist")
            )
        ).scalar_one()
    from html import escape

    return "\n".join(
        [
            f"<b>id {ev.id}</b>: {escape(ev.title)}",
            f"Спикер: {escape(ev.speaker)}",
            f"starts_at: {ev.starts_at.astimezone(MSK):%d.%m.%Y %H:%M} МСК",
            f"venue_text: {escape(ev.venue_text or '—')}",
            f"venue_url: {escape(ev.venue_url or '—')}",
            f"seats: {ev.seats}",
            f"registration_limit: {ev.registration_limit}",
            f"is_open: {'true' if ev.is_open else 'false'}",
            f"Зарегистрировано: {registered}, лист ожидания: {waitlist}",
            f"shared_widget_id: {ev.shared_widget_id or '—'}",
            "",
            "Изменить: /event_set &lt;поле&gt; &lt;значение&gt;, поля: " + ", ".join(EDITABLE_FIELDS),
            "starts_at в формате ДД.ММ.ГГГГ ЧЧ:ММ (МСК)",
        ]
    )


class BadValue(ValueError):
    pass


def parse_field_value(field: str, raw: str):
    raw = raw.strip()
    if field not in EDITABLE_FIELDS:
        raise BadValue("Поле нельзя менять. Доступны: " + ", ".join(EDITABLE_FIELDS))
    if field == "is_open":
        if raw.lower() not in ("true", "false", "1", "0", "да", "нет"):
            raise BadValue("is_open: true или false")
        return raw.lower() in ("true", "1", "да")
    if field == "registration_limit":
        if not raw.isdigit() or not 1 <= int(raw) <= 10000:
            raise BadValue("registration_limit: целое число от 1")
        return int(raw)
    if field == "starts_at":
        try:
            return datetime.strptime(raw, "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
        except ValueError as e:
            raise BadValue("starts_at: ДД.ММ.ГГГГ ЧЧ:ММ по Москве") from e
    if field == "venue_url":
        if raw in ("-", "—"):
            return None
        if not raw.startswith("https://") or len(raw) > 500:
            raise BadValue("venue_url: ссылка https://… или «-», чтобы убрать")
        return raw
    if field == "venue_text":
        if not 3 <= len(raw) <= 300:
            raise BadValue("venue_text: от 3 до 300 символов")
        return raw
    raise BadValue("unknown field")


async def set_event_field(sm: async_sessionmaker, event_id: int, field: str, value) -> None:
    values = {field: value}
    if field in ("venue_text", "starts_at"):
        # HTML карточки неизменяем: адрес и время в нём «зашиты», поэтому нужен новый shared-виджет
        values["shared_widget_id"] = None
    async with sm() as s, s.begin():
        await s.execute(update(Event).where(Event.id == event_id).values(**values))
