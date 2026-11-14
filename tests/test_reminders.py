"""Напоминания: каждое ровно один раз, статусы билета, 403, повтор после сбоя."""
import json
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import text, update

from bot.db.models import Registration
from bot.services import registration as reg
from bot.services.delivery import Delivery
from bot.services.reminders import FEEDBACK_TEXT, Reminders
from bot.widgets.service import WidgetService
from tests.fake_telegram import callback_update, make_bot
from tests.factories import profile

T = (datetime.now(timezone.utc) + timedelta(days=3)).replace(second=0, microsecond=0)
H = timedelta(hours=1)


@pytest_asyncio.fixture
async def renv(sessionmaker, engine):
    async with engine.begin() as c:
        await c.execute(text("UPDATE events SET starts_at=:t WHERE id=1"), {"t": T})
    bot, session = make_bot()
    widgets = WidgetService(bot, sessionmaker, autoflush=False)
    delivery = Delivery(bot, sessionmaker, widgets=widgets)
    for uid in (1, 2, 3):
        await reg.complete_registration(sessionmaker, uid, 1, profile(), None)
        await delivery.send_ticket((await _reg(sessionmaker, uid)).id)  # отправит виджет-билет
    await reg.cancel(sessionmaker, 3, 1)  # отменённым не напоминаем
    session.reset()
    return session, Reminders(sessionmaker, delivery)


async def _reg(sm, uid):
    async with sm() as s:
        return await reg.get_registration(s, uid, 1)


def _msgs(session, uid=None):
    return [p for n, p in session.calls if n == "SendMessage" and (uid is None or p["chat_id"] == uid)]


def _statuses(session):
    return [json.loads(p["chat_data"])["s"] for n, p in session.calls if n == "EditMessageWidgetData"]


async def test_each_stage_exactly_once(renv, sessionmaker):
    session, rem = renv
    assert await rem.tick(T - 25 * H) == {}

    assert await rem.tick(T - 23 * H) == {"reminded_24h": 2}
    texts = [m["text"] for m in _msgs(session)]
    assert len(texts) == 2 and all("Напоминаем" in x and "Зубовский бульвар" in x for x in texts)
    assert await rem.tick(T - 22 * H) == {}  # второй раз не уходит

    session.reset()
    assert await rem.tick(T - 1 * H) == {"reminded_2h": 2}
    assert all(m["text"].startswith("Начинаем через 2 часа") for m in _msgs(session))
    assert _statuses(session) == ["soon", "soon"]

    session.reset()
    assert await rem.tick(T + timedelta(minutes=1)) == {"live_sent": 2}
    assert _msgs(session) == [] and _statuses(session) == ["live", "live"]

    session.reset()
    assert await rem.tick(T + 3 * H) == {"feedback_sent": 2}
    assert [m["text"] for m in _msgs(session)] == [FEEDBACK_TEXT] * 2
    assert _statuses(session) == ["done", "done"]

    session.reset()
    for hours in (4, 10, 30):
        assert await rem.tick(T + hours * H) == {}
    assert session.calls == []
    assert not _msgs(session, 3)


async def test_downtime_skips_stale_stages(renv):
    session, rem = renv
    # бот лежал до T+1ч: напоминания за сутки и за 2 часа уже не нужны, билет сразу «live»
    assert await rem.tick(T + 1 * H) == {"live_sent": 2}
    assert _msgs(session) == []
    assert await rem.tick(T + 3 * H) == {"feedback_sent": 2}


async def test_blocked_user_is_skipped_and_failure_retried(renv, sessionmaker):
    session, rem = renv
    from aiogram.exceptions import TelegramForbiddenError

    real = session.make_request

    async def flaky(bot, method, timeout=None):
        chat = getattr(method, "chat_id", None)
        if type(method).__name__ == "SendMessage" and chat == 1:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if type(method).__name__ == "SendMessage" and chat == 2 and not flaky.failed:
            flaky.failed = True
            raise RuntimeError("network down")
        return await real(bot, method, timeout)

    flaky.failed = False
    session.make_request = flaky
    assert await rem.tick(T - 23 * H) == {"reminded_24h": 1}  # 403 считаем обработанным, сбой откатили
    assert await rem.tick(T - 23 * H + timedelta(minutes=1)) == {"reminded_24h": 1}  # повтор для второго
    assert await rem.tick(T - 22 * H) == {}


async def test_late_registrant_gets_no_day_reminder(renv, sessionmaker, engine):
    session, rem = renv
    # «записался» за 3 часа до начала
    async with engine.begin() as c:
        await c.execute(text("UPDATE registrations SET created_at=:t WHERE user_id=2"), {"t": T - 3 * H})
    assert await rem.tick(T - 150 * timedelta(minutes=1)) == {"reminded_24h": 1}
    assert [m["chat_id"] for m in _msgs(session)] == [1]


async def test_rating_button(renv, sessionmaker):
    session, rem = renv
    from bot.app import build_dispatcher
    from tests import test_bot_flow

    await rem.tick(T + 1 * H)
    await rem.tick(T + 3 * H)
    dp = test_bot_flow._DP.get("dp") or build_dispatcher(sessionmaker, rem.delivery)
    test_bot_flow._DP["dp"] = dp
    dp["sm"], dp["delivery"] = sessionmaker, rem.delivery
    session.reset()
    await dp.feed_update(rem.bot, callback_update(1, "rate:1:4"))
    assert (await _reg(sessionmaker, 1)).feedback_score == 4
    assert "Твоя оценка: 4 из 5" in session.texts()[-1]
    chat = json.loads(session.params("EditMessageWidgetData")[0]["chat_data"])
    assert chat["s"] == "done" and chat["sc"] == 4
