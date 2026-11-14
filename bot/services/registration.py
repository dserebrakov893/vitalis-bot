"""Регистрация, лимит мест, лист ожидания, отмена и удаление данных.

Все операции с местами идут в транзакции с блокировкой строки события (SELECT ... FOR UPDATE),
поэтому параллельные регистрации не превышают лимит, а номера билетов не дублируются.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import Consent, DeletionLog, Event, FsmState, Registration, User

PDN_TEXT_VERSION = "pdn-2026-11-14"
NEWSLETTER_TEXT_VERSION = "newsletter-2026-11-14"
ACTIVE = ("registered", "waitlist")
# Регистрация считается «текущей» до T+6ч: нужно для /ticket и оценки после встречи
_AFTER_EVENT = timedelta(hours=6)


class RegistrationClosed(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Profile:
    full_name: str
    phone: str
    email: str
    university: str
    status: str
    interests: list[str]
    project_stage: str
    age_group: str
    newsletter_opt_in: bool


@dataclass
class RegResult:
    registration: Registration
    created: bool  # False: человек уже был в списке (двойной тап), ничего не меняли
    waitlist_position: int | None = None


@dataclass
class CancelResult:
    cancelled: Registration | None
    promoted: list[Registration] = field(default_factory=list)


# ---------- чтение ----------

async def get_current_event(session: AsyncSession, now: datetime | None = None) -> Event | None:
    """Ближайшая открытая встреча, которая ещё не началась."""
    now = now or utcnow()
    return (
        await session.execute(
            select(Event).where(Event.is_open.is_(True), Event.starts_at > now).order_by(Event.starts_at).limit(1)
        )
    ).scalar_one_or_none()


async def get_registration(session: AsyncSession, user_id: int, event_id: int) -> Registration | None:
    return (
        await session.execute(
            select(Registration).where(Registration.user_id == user_id, Registration.event_id == event_id)
        )
    ).scalar_one_or_none()


async def get_active_registration(session: AsyncSession, user_id: int, now: datetime | None = None) -> Registration | None:
    """Активная регистрация пользователя на ближайшую ещё не закончившуюся встречу."""
    now = now or utcnow()
    return (
        await session.execute(
            select(Registration)
            .join(Event)
            .where(
                Registration.user_id == user_id,
                Registration.state.in_(ACTIVE),
                Event.starts_at > now - _AFTER_EVENT,
            )
            .order_by(Event.starts_at)
            .limit(1)
        )
    ).scalar_one_or_none()


async def count_registered(session: AsyncSession, event_id: int) -> int:
    return (
        await session.execute(
            select(func.count()).where(Registration.event_id == event_id, Registration.state == "registered")
        )
    ).scalar_one()


async def seats_left(session: AsyncSession, event: Event) -> int:
    return max(0, event.registration_limit - await count_registered(session, event.id))


async def waitlist_position(session: AsyncSession, reg: Registration) -> int | None:
    if reg.state != "waitlist":
        return None
    return (
        await session.execute(
            select(func.count()).where(
                Registration.event_id == reg.event_id,
                Registration.state == "waitlist",
                or_(
                    Registration.waitlisted_at < reg.waitlisted_at,
                    and_(Registration.waitlisted_at == reg.waitlisted_at, Registration.id <= reg.id),
                ),
            )
        )
    ).scalar_one()


# ---------- пользователь и согласия ----------

async def touch_user(
    session: AsyncSession, user_id: int, username: str | None, first_name: str | None, source: str | None
) -> User:
    """Создаёт пользователя при первом входе. users.source пишем только один раз."""
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, tg_first_name=first_name, source=source, interests=[])
        session.add(user)
    else:
        user.username = username
        user.tg_first_name = first_name
        if user.source is None and source:
            user.source = source
    await session.flush()
    return user


async def _save_profile(session: AsyncSession, user_id: int, p: Profile) -> None:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        user = User(id=user_id, interests=[])
        session.add(user)
    user.full_name = p.full_name
    user.phone = p.phone
    user.email = p.email
    user.university = p.university
    user.status = p.status
    user.interests = list(p.interests)
    user.project_stage = p.project_stage
    user.age_group = p.age_group
    user.newsletter_opt_in = p.newsletter_opt_in
    await session.flush()

    session.add(Consent(user_id=user_id, type="pdn", text_version=PDN_TEXT_VERSION))
    if p.newsletter_opt_in:
        has = (
            await session.execute(
                select(Consent.id).where(
                    Consent.user_id == user_id, Consent.type == "newsletter", Consent.revoked_at.is_(None)
                )
            )
        ).first()
        if not has:
            session.add(Consent(user_id=user_id, type="newsletter", text_version=NEWSLETTER_TEXT_VERSION))
    else:
        await _revoke_newsletter(session, user_id)


async def _revoke_newsletter(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(Consent)
        .where(Consent.user_id == user_id, Consent.type == "newsletter", Consent.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


async def unsubscribe(sm: async_sessionmaker, user_id: int) -> bool:
    async with sm() as s, s.begin():
        user = await s.get(User, user_id, with_for_update=True)
        if user is None:
            return False
        user.newsletter_opt_in = False
        await _revoke_newsletter(s, user_id)
        return True


# ---------- места ----------

async def _lock_event(session: AsyncSession, event_id: int) -> Event:
    ev = (
        await session.execute(select(Event).where(Event.id == event_id).with_for_update())
    ).scalar_one_or_none()
    if ev is None:
        raise RegistrationClosed("event not found")
    return ev


async def _next_ticket_no(session: AsyncSession, event_id: int) -> int:
    # Номера не переиспользуются: у отменённых регистраций номер остаётся аннулированным
    current = (
        await session.execute(select(func.max(Registration.ticket_no)).where(Registration.event_id == event_id))
    ).scalar_one()
    return (current or 0) + 1


async def _register_locked(
    session: AsyncSession, ev: Event, user_id: int, source: str | None, now: datetime
) -> RegResult:
    if not ev.is_open or now >= ev.starts_at:
        raise RegistrationClosed()
    reg = (
        await session.execute(
            select(Registration)
            .where(Registration.user_id == user_id, Registration.event_id == ev.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if reg is not None and reg.state in ACTIVE:
        return RegResult(reg, created=False, waitlist_position=await waitlist_position(session, reg))

    # Решение принимаем до того, как объект попадёт в сессию (иначе autoflush вставит пустую строку)
    has_place = await count_registered(session, ev.id) < ev.registration_limit
    ticket_no = await _next_ticket_no(session, ev.id) if has_place else None

    if reg is None:
        reg = Registration(user_id=user_id, event_id=ev.id)
        session.add(reg)
    reg.source = source
    reg.reminded_24h = reg.reminded_2h = reg.live_sent = reg.feedback_sent = False
    reg.ticket_widget_msg_id = None
    reg.ticket_widget_status = None
    reg.state = "registered" if has_place else "waitlist"
    reg.ticket_no = ticket_no
    reg.waitlisted_at = None if has_place else now
    await session.flush()
    return RegResult(reg, created=True, waitlist_position=await waitlist_position(session, reg))


async def _fill_from_waitlist(session: AsyncSession, ev: Event) -> list[Registration]:
    """Пока есть места, отдаём их первым из листа ожидания. Событие уже заблокировано."""
    promoted: list[Registration] = []
    free = ev.registration_limit - await count_registered(session, ev.id)
    if free <= 0:
        return promoted
    queue = (
        await session.execute(
            select(Registration)
            .where(Registration.event_id == ev.id, Registration.state == "waitlist")
            .order_by(Registration.waitlisted_at, Registration.id)
            .limit(free)
            .with_for_update()
        )
    ).scalars().all()
    for reg in queue:
        reg.state = "registered"
        reg.ticket_no = await _next_ticket_no(session, ev.id)
        reg.waitlisted_at = None
        await session.flush()
        promoted.append(reg)
    return promoted


async def complete_registration(
    sm: async_sessionmaker,
    user_id: int,
    event_id: int,
    profile: Profile,
    source: str | None,
    now: datetime | None = None,
) -> RegResult:
    """Анкета + согласия + регистрация одной транзакцией."""
    now = now or utcnow()
    async with sm() as s, s.begin():
        ev = await _lock_event(s, event_id)
        if not ev.is_open or now >= ev.starts_at:
            raise RegistrationClosed()
        existing = await get_registration(s, user_id, event_id)
        if existing is not None and existing.state in ACTIVE:
            # двойной тап на последнем шаге: анкету и согласия второй раз не пишем
            return RegResult(existing, created=False, waitlist_position=await waitlist_position(s, existing))
        await _save_profile(s, user_id, profile)
        return await _register_locked(s, ev, user_id, source, now)


async def register(
    sm: async_sessionmaker, user_id: int, event_id: int, source: str | None, now: datetime | None = None
) -> RegResult:
    """Регистрация уже заполнившего анкету человека (повторная встреча или повторный тап)."""
    now = now or utcnow()
    async with sm() as s, s.begin():
        ev = await _lock_event(s, event_id)
        return await _register_locked(s, ev, user_id, source, now)


async def cancel(sm: async_sessionmaker, user_id: int, event_id: int, now: datetime | None = None) -> CancelResult:
    now = now or utcnow()
    async with sm() as s, s.begin():
        ev = await _lock_event(s, event_id)
        reg = (
            await s.execute(
                select(Registration)
                .where(Registration.user_id == user_id, Registration.event_id == event_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if reg is None or reg.state not in ACTIVE:
            return CancelResult(None)
        was_registered = reg.state == "registered"
        reg.state = "cancelled"
        reg.waitlisted_at = None
        await s.flush()
        promoted = await _fill_from_waitlist(s, ev) if was_registered and now < ev.starts_at else []
        return CancelResult(reg, promoted)


async def fill_waitlist(sm: async_sessionmaker, event_id: int, now: datetime | None = None) -> list[Registration]:
    """После увеличения лимита админом."""
    now = now or utcnow()
    async with sm() as s, s.begin():
        ev = await _lock_event(s, event_id)
        if now >= ev.starts_at:
            return []
        return await _fill_from_waitlist(s, ev)


async def delete_user(sm: async_sessionmaker, user_id: int, now: datetime | None = None) -> list[Registration]:
    """/delete_me: освобождаем места (лист ожидания продвигается), удаляем все ПДн каскадно.

    В журнале остаются только Telegram ID и дата. Возвращает продвинутые из листа ожидания регистрации."""
    promoted: list[Registration] = []
    async with sm() as s:
        event_ids = (
            await s.execute(
                select(Registration.event_id).where(
                    Registration.user_id == user_id, Registration.state.in_(ACTIVE)
                )
            )
        ).scalars().all()
    for event_id in event_ids:
        promoted += (await cancel(sm, user_id, event_id, now)).promoted
    async with sm() as s, s.begin():
        await s.execute(delete(User).where(User.id == user_id))
        await s.execute(delete(FsmState).where(FsmState.key.like(f"%:{user_id}:{user_id}:%")))
        s.add(DeletionLog(telegram_id=user_id))
    return promoted

