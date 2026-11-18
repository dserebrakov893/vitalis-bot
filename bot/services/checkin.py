"""Чекин на входе: проверка QR, поиск, отметка, доступ проверяющих.

Проверяющий видит только ФИО и номер билета: телефона, почты и анкеты в ответах нет.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import MSK
from bot.db.models import Checker, CheckerInvite, Event, Registration, User
from bot.formatting import event_end
from bot.services.registration import utcnow
from bot.services.tickets import parse_payload

# Окно, в котором встреча считается «текущей» для чекина
BEFORE_START = timedelta(hours=12)
AFTER_END = timedelta(hours=6)
CHECKER_GRACE = timedelta(hours=2)  # доступ проверяющего истекает через 2 часа после окончания
INVITE_TTL = timedelta(hours=48)
SEARCH_LIMIT = 20


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def checker_expires(event: Event) -> datetime:
    return event_end(event) + CHECKER_GRACE


# ---------- какая встреча и кто имеет доступ ----------

async def checkin_event(sm: async_sessionmaker, now: datetime | None = None) -> Event | None:
    """Встреча для чекина: идущая сейчас (с запасом до и после), иначе ближайшая предстоящая."""
    now = now or utcnow()
    async with sm() as s:
        events = (await s.execute(select(Event).order_by(Event.starts_at))).scalars().all()
    live = [e for e in events if e.starts_at - BEFORE_START <= now <= event_end(e) + AFTER_END]
    if live:
        return min(live, key=lambda e: abs((e.starts_at - now).total_seconds()))
    upcoming = [e for e in events if e.starts_at > now]
    return upcoming[0] if upcoming else None


async def access_event(sm: async_sessionmaker, user_id: int, admin_ids, now: datetime | None = None) -> Event | None:
    """Встреча, к чекину которой у человека есть доступ, или None."""
    now = now or utcnow()
    if user_id in admin_ids:
        return await checkin_event(sm, now)
    async with sm() as s:
        row = (
            await s.execute(
                select(Event)
                .join(Checker, Checker.event_id == Event.id)
                .where(Checker.user_id == user_id, Checker.revoked_at.is_(None), Checker.expires_at > now)
                .order_by(Event.starts_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


# ---------- приглашения ----------

async def create_invite(sm: async_sessionmaker, event: Event, admin_id: int, now: datetime | None = None) -> str:
    now = now or utcnow()
    token = secrets.token_urlsafe(16)  # 22 символа, подходит для start-параметра
    async with sm() as s, s.begin():
        s.add(CheckerInvite(
            token_hash=_hash(token), event_id=event.id, created_by=admin_id,
            expires_at=min(now + INVITE_TTL, checker_expires(event)),
        ))
    return token


@dataclass
class RedeemResult:
    ok: bool
    reason: str = ""  # used / expired / unknown
    event: Event | None = None


async def redeem_invite(sm: async_sessionmaker, token: str, user_id: int, now: datetime | None = None) -> RedeemResult:
    now = now or utcnow()
    async with sm() as s, s.begin():
        inv = (
            await s.execute(select(CheckerInvite).where(CheckerInvite.token_hash == _hash(token)).with_for_update())
        ).scalar_one_or_none()
        if inv is None:
            return RedeemResult(False, "unknown")
        event = await s.get(Event, inv.event_id)
        if inv.used_by is not None:
            # повторное открытие той же ссылки тем же человеком: доступ уже есть, это не ошибка
            if inv.used_by == user_id:
                return RedeemResult(True, event=event)
            return RedeemResult(False, "used")
        if inv.expires_at <= now or event is None:
            return RedeemResult(False, "expired")
        inv.used_by, inv.used_at = user_id, now
        stmt = insert(Checker).values(
            user_id=user_id, event_id=event.id, added_by=inv.created_by, expires_at=checker_expires(event)
        )
        await s.execute(stmt.on_conflict_do_update(
            constraint="uq_checker_user_event",
            set_={"revoked_at": None, "expires_at": checker_expires(event)},
        ))
        return RedeemResult(True, event=event)


async def list_checkers(sm: async_sessionmaker, event: Event, now: datetime | None = None) -> list[tuple[int, str]]:
    now = now or utcnow()
    async with sm() as s:
        rows = (
            await s.execute(
                select(Checker.user_id, User.username, User.tg_first_name)
                .outerjoin(User, User.id == Checker.user_id)
                .where(Checker.event_id == event.id, Checker.revoked_at.is_(None), Checker.expires_at > now)
                .order_by(Checker.created_at)
            )
        ).all()
    return [(uid, f"@{un}" if un else (name or str(uid))) for uid, un, name in rows]


async def revoke_all(sm: async_sessionmaker, event: Event) -> int:
    async with sm() as s, s.begin():
        res = await s.execute(
            update(Checker).where(Checker.event_id == event.id, Checker.revoked_at.is_(None)).values(revoked_at=func.now())
        )
        return res.rowcount or 0


# ---------- счётчик, скан, поиск, отметка ----------

async def counts(sm: async_sessionmaker, event_id: int) -> tuple[int, int]:
    """(пришло, всего зарегистрировано)."""
    async with sm() as s:
        total, came = (
            await s.execute(
                select(func.count(), func.count(Registration.checked_in_at)).where(
                    Registration.event_id == event_id, Registration.state == "registered"
                )
            )
        ).one()
    return came, total


def _msk_time(dt: datetime | None) -> str | None:
    return dt.astimezone(MSK).strftime("%H:%M") if dt else None


def _person(reg: Registration, user: User | None) -> dict:
    """Только то, что нужно на входе: ФИО и номер билета."""
    return {
        "id": reg.id,
        "name": (user.full_name or user.tg_first_name or "") if user else "",
        "ticket_no": reg.ticket_no,
        "checked_at": _msk_time(reg.checked_in_at),
    }


async def _mark(sm: async_sessionmaker, reg_id: int, by: int) -> tuple[bool, Registration | None, User | None]:
    """Атомарная отметка: True, если отметили сейчас; False, если уже была отмечена."""
    async with sm() as s, s.begin():
        res = await s.execute(
            update(Registration)
            .where(Registration.id == reg_id, Registration.state == "registered", Registration.checked_in_at.is_(None))
            .values(checked_in_at=func.now(), checked_in_by=by)
            .returning(Registration.id)
        )
        marked = res.first() is not None
        reg = await s.get(Registration, reg_id, populate_existing=True)
        user = await s.get(User, reg.user_id) if reg else None
    return marked, reg, user


async def scan(sm: async_sessionmaker, event: Event, payload: str, by: int) -> dict:
    """Результат скана QR: ok (зелёный), already (жёлтый), invalid / other_event / cancelled (красный)."""
    parsed = parse_payload(payload)
    if parsed is None:
        return {"status": "invalid"}
    event_id, ticket_no = parsed
    if event_id != event.id:
        return {"status": "other_event"}
    async with sm() as s:
        reg = (
            await s.execute(
                select(Registration).where(Registration.event_id == event_id, Registration.ticket_no == ticket_no)
            )
        ).scalar_one_or_none()
    if reg is None or reg.state != "registered":
        return {"status": "cancelled"}  # отменён, удалён по /delete_me или номер не выдавался
    marked, reg, user = await _mark(sm, reg.id, by)
    return {"status": "ok" if marked else "already", **_person(reg, user)}


async def mark(sm: async_sessionmaker, event: Event, reg_id: int, by: int) -> dict:
    async with sm() as s:
        reg = await s.get(Registration, reg_id)
    if reg is None or reg.event_id != event.id or reg.state != "registered":
        return {"status": "cancelled"}
    marked, reg, user = await _mark(sm, reg_id, by)
    return {"status": "ok" if marked else "already", **_person(reg, user)}


async def unmark(sm: async_sessionmaker, event: Event, reg_id: int) -> dict:
    """Снять ошибочную отметку."""
    async with sm() as s, s.begin():
        reg = await s.get(Registration, reg_id, with_for_update=True)
        if reg is None or reg.event_id != event.id:
            return {"status": "cancelled"}
        reg.checked_in_at = None
        reg.checked_in_by = None
        user = await s.get(User, reg.user_id)
        await s.flush()
        return {"status": "unmarked", **_person(reg, user)}


async def search(sm: async_sessionmaker, event: Event, query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    cond = Registration.ticket_no == int(q) if q.isdigit() else None
    async with sm() as s:
        stmt = (
            select(Registration, User)
            .join(User, User.id == Registration.user_id)
            .where(Registration.event_id == event.id, Registration.state == "registered")
        )
        if cond is not None:
            stmt = stmt.where(cond)
        else:
            cleaned = q.lower().replace("ё", "е").replace("%", "").replace("_", "").replace("\\", "").strip()
            if not cleaned:
                return []
            pattern = "%" + cleaned + "%"
            stmt = stmt.where(func.replace(func.lower(User.full_name), "ё", "е").like(pattern))
        rows = (await s.execute(stmt.order_by(User.full_name).limit(SEARCH_LIMIT))).all()
    return [_person(r, u) for r, u in rows]
