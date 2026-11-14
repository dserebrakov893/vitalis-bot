import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from bot.db.models import Consent, Registration, User
from bot.services import registration as reg
from tests.factories import add_users, profile

BEFORE = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
AFTER_START = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)


async def _states(sm):
    async with sm() as s:
        rows = (await s.execute(select(Registration.state, func.count()).group_by(Registration.state))).all()
    return dict(rows)


async def test_76th_goes_to_waitlist(sessionmaker):
    await add_users(sessionmaker, range(1, 77))
    results = [await reg.register(sessionmaker, uid, 1, "test", BEFORE) for uid in range(1, 77)]
    assert [r.registration.ticket_no for r in results[:75]] == list(range(1, 76))
    last = results[75]
    assert last.registration.state == "waitlist"
    assert last.registration.ticket_no is None
    assert last.waitlist_position == 1
    assert await _states(sessionmaker) == {"registered": 75, "waitlist": 1}


async def test_cancel_frees_place_for_first_in_waitlist(sessionmaker):
    await add_users(sessionmaker, range(1, 79))
    for uid in range(1, 79):  # 75 мест + 3 в листе ожидания: 76, 77, 78
        await reg.register(sessionmaker, uid, 1, None, BEFORE)

    res = await reg.cancel(sessionmaker, 10, 1, BEFORE)
    assert res.cancelled.state == "cancelled"
    assert [p.user_id for p in res.promoted] == [76]
    assert res.promoted[0].ticket_no == 76  # номер 10 аннулирован, не переиспользуется
    assert await _states(sessionmaker) == {"registered": 75, "waitlist": 2, "cancelled": 1}

    async with sessionmaker() as s:
        r77 = await reg.get_registration(s, 77, 1)
        assert await reg.waitlist_position(s, r77) == 1


async def test_waitlist_cancel_does_not_promote(sessionmaker):
    await add_users(sessionmaker, range(1, 78))
    for uid in range(1, 78):
        await reg.register(sessionmaker, uid, 1, None, BEFORE)
    res = await reg.cancel(sessionmaker, 76, 1, BEFORE)
    assert res.cancelled is not None and res.promoted == []
    async with sessionmaker() as s:
        assert await reg.waitlist_position(s, await reg.get_registration(s, 77, 1)) == 1


async def test_parallel_registrations_do_not_exceed_limit(sessionmaker):
    await add_users(sessionmaker, range(1, 121))
    results = await asyncio.gather(*(reg.register(sessionmaker, uid, 1, None, BEFORE) for uid in range(1, 121)))
    tickets = sorted(r.registration.ticket_no for r in results if r.registration.state == "registered")
    assert tickets == list(range(1, 76))
    assert await _states(sessionmaker) == {"registered": 75, "waitlist": 45}
    positions = sorted(r.waitlist_position for r in results if r.registration.state == "waitlist")
    assert positions == list(range(1, 46))


async def test_parallel_cancels_promote_each_once(sessionmaker):
    await add_users(sessionmaker, range(1, 86))
    for uid in range(1, 86):
        await reg.register(sessionmaker, uid, 1, None, BEFORE)
    results = await asyncio.gather(*(reg.cancel(sessionmaker, uid, 1, BEFORE) for uid in range(1, 11)))
    promoted = sorted(p.user_id for r in results for p in r.promoted)
    assert promoted == list(range(76, 86))
    assert await _states(sessionmaker) == {"registered": 75, "cancelled": 10}


async def test_double_tap_is_idempotent(sessionmaker):
    await add_users(sessionmaker, [1])
    first, second = await asyncio.gather(
        reg.complete_registration(sessionmaker, 1, 1, profile(), "test", BEFORE),
        reg.complete_registration(sessionmaker, 1, 1, profile(), "test", BEFORE),
    )
    assert sorted([first.created, second.created]) == [False, True]
    assert first.registration.ticket_no == second.registration.ticket_no == 1
    assert await _states(sessionmaker) == {"registered": 1}


async def test_registration_closes_at_start(sessionmaker):
    await add_users(sessionmaker, [1])
    with pytest.raises(reg.RegistrationClosed):
        await reg.register(sessionmaker, 1, 1, None, AFTER_START)
    async with sessionmaker() as s:
        assert await reg.get_current_event(s, AFTER_START) is None
        assert (await reg.get_current_event(s, BEFORE)).id == 1


async def test_reregister_after_cancel_gets_new_ticket(sessionmaker):
    await add_users(sessionmaker, [1, 2])
    await reg.register(sessionmaker, 1, 1, "a", BEFORE)
    await reg.register(sessionmaker, 2, 1, "a", BEFORE)
    await reg.cancel(sessionmaker, 1, 1, BEFORE)
    again = await reg.register(sessionmaker, 1, 1, "b", BEFORE)
    assert again.created and again.registration.ticket_no == 3 and again.registration.source == "b"


async def test_complete_registration_saves_profile_and_consents(sessionmaker):
    async with sessionmaker() as s, s.begin():
        await reg.touch_user(s, 5, "ivan", "Иван", "insta")
        await reg.touch_user(s, 5, "ivan", "Иван", "other")  # первый источник не перезаписывается
    res = await reg.complete_registration(sessionmaker, 5, 1, profile(newsletter=True), "insta", BEFORE)
    assert res.registration.ticket_no == 1
    async with sessionmaker() as s:
        user = await s.get(User, 5)
        consents = (await s.execute(select(Consent.type, Consent.text_version))).all()
    assert user.source == "insta" and user.full_name == "Иванов Иван Иванович" and user.newsletter_opt_in
    assert sorted(consents) == [("newsletter", reg.NEWSLETTER_TEXT_VERSION), ("pdn", reg.PDN_TEXT_VERSION)]

    assert await reg.unsubscribe(sessionmaker, 5)
    async with sessionmaker() as s:
        assert (await s.get(User, 5)).newsletter_opt_in is False
        revoked = (await s.execute(select(Consent.revoked_at).where(Consent.type == "newsletter"))).scalar_one()
    assert revoked is not None


async def test_limit_increase_fills_waitlist(sessionmaker, engine):
    from sqlalchemy import text

    await add_users(sessionmaker, range(1, 79))
    for uid in range(1, 79):
        await reg.register(sessionmaker, uid, 1, None, BEFORE)
    async with engine.begin() as c:
        await c.execute(text("UPDATE events SET registration_limit=77 WHERE id=1"))
    promoted = await reg.fill_waitlist(sessionmaker, 1, BEFORE)
    assert [p.user_id for p in promoted] == [76, 77]
