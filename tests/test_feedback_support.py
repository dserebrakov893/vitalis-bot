"""Отзывы (опрос после встречи, кнопка «Отзыв», отзыв о клубе), поддержка, /event_new, /feedback."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select, text

from bot import texts as t
from bot.config import MSK
from bot.db.models import Event, Feedback, User
from bot.handlers import admin
from bot.services import registration as reg
from bot.services.delivery import Delivery
from tests import test_bot_flow
from tests.factories import profile
from tests.fake_telegram import callback_update, make_bot, text_update

UID = 701
ADMIN = 42
SUPPORT = 900


@pytest_asyncio.fixture
async def fenv(sessionmaker, engine):
    from bot.app import build_dispatcher

    bot, session = make_bot()
    delivery = Delivery(bot, sessionmaker)
    if "dp" not in test_bot_flow._DP:
        test_bot_flow._DP["dp"] = build_dispatcher(sessionmaker, delivery)
    dp = test_bot_flow._DP["dp"]
    dp["sm"], dp["delivery"] = sessionmaker, delivery
    admin.ADMIN_IDS.add(ADMIN)

    async def send(update):
        await dp.feed_update(bot, update)

    async def past_event():
        # встреча закончилась час назад: опрос уже открыт
        async with engine.begin() as c:
            await c.execute(text(
                "UPDATE events SET starts_at=now()-interval '4 hours', ends_at=now()-interval '1 hour' WHERE id=1"))

    yield session, send, past_event
    admin.ADMIN_IDS.discard(ADMIN)


async def _registered(sm, uid=UID):
    await reg.complete_registration(sm, uid, 1, profile(), "test", datetime(2026, 11, 16, tzinfo=timezone.utc))


async def _fb(sm, uid=UID):
    async with sm() as s:
        return (await s.execute(select(Feedback).where(Feedback.user_id == uid))).scalars().all()


async def test_event_feedback_full_flow(fenv, sessionmaker):
    session, send, past_event = fenv
    await _registered(sessionmaker)
    await past_event()

    await send(text_update(UID, t.MENU_FEEDBACK))
    choose = session.params("SendMessage")[-1]
    buttons = [b["text"] for row in choose["reply_markup"]["inline_keyboard"] for b in row]
    assert choose["text"] == t.FB_CHOOSE and buttons == ["О встрече с Семёном Покацким", t.FB_CLUB_BTN]

    await send(callback_update(UID, "fb:ev:1"))
    assert session.last_text() == t.fb_rate_text("Встреча с Семёном Покацким")
    await send(callback_update(UID, "fb:r:1:2"))
    assert session.last_text() == t.FB_IMPROVE  # низкая оценка: что улучшить
    await send(callback_update(UID, "fb:t:venue"))
    await send(callback_update(UID, "fb:t:time"))
    await send(callback_update(UID, "fb:t:venue"))  # снять галочку
    await send(text_update(UID, "просто текст"))
    assert session.last_text() == t.USE_BUTTONS
    await send(callback_update(UID, "fb:td"))
    assert session.last_text() == t.FB_COMMENT
    await send(text_update(UID, t.MENU_TICKET))  # кнопка внизу не становится комментарием
    await send(text_update(UID, "x" * 1001))
    assert session.last_text() == t.FB_TOO_LONG
    await send(text_update(UID, "Начали на 20 минут позже"))
    assert session.last_text() == t.FB_NEXT
    await send(text_update(UID, "Про регуляторику медизделий"))
    assert session.last_text() == t.FB_THANKS

    [f] = await _fb(sessionmaker)
    assert (f.kind, f.score, f.attended, f.tags) == ("event", 2, True, ["time"])
    assert f.comment == "Начали на 20 минут позже" and f.next_topic == "Про регуляторику медизделий"

    # повторный вход: не вторая запись, а «изменить или дописать»
    await send(text_update(UID, t.MENU_FEEDBACK))
    await send(callback_update(UID, "fb:ev:1"))
    assert session.last_text() == t.fb_already_text(2, True)
    await send(callback_update(UID, "fb:rerate:1"))
    await send(callback_update(UID, "fb:r:1:5"))
    assert session.last_text() == t.FB_LIKED  # высокая оценка: что понравилось
    await send(callback_update(UID, "fb:ts"))
    await send(callback_update(UID, "fb:skip:comment"))
    await send(callback_update(UID, "fb:skip:next"))
    [f] = await _fb(sessionmaker)
    assert f.score == 5 and f.comment == "Начали на 20 минут позже"  # пропуск не стирает старое
    async with sessionmaker() as s:
        assert (await reg.get_registration(s, UID, 1)).feedback_score == 5

    await send(callback_update(UID, "fb:addc:1"))
    assert session.last_text() == t.FB_COMMENT_ONLY
    await send(text_update(UID, "Спасибо за встречу"))
    assert session.last_text() == t.FB_THANKS  # только комментарий, без вопроса о следующей встрече
    [f] = await _fb(sessionmaker)
    assert f.comment == "Спасибо за встречу"


async def test_absent_and_closed(fenv, sessionmaker, engine):
    session, send, past_event = fenv
    await _registered(sessionmaker)
    await past_event()
    await send(callback_update(UID, "fb:r:1:0"))
    assert session.last_text() == t.FB_ABSENT_THANKS
    [f] = await _fb(sessionmaker)
    assert f.attended is False and f.score is None

    # через 8 дней после опроса отзыв о встрече закрыт, кнопка ведёт сразу в отзыв о клубе
    async with engine.begin() as c:
        await c.execute(text("UPDATE events SET starts_at=now()-interval '9 days', ends_at=now()-interval '9 days'"))
    await send(callback_update(UID, "fb:r:1:4"))
    assert [f.score for f in await _fb(sessionmaker)] == [None]
    await send(text_update(UID, t.MENU_FEEDBACK))
    assert session.last_text() == t.FB_CLUB_CATEGORY


async def test_club_feedback(fenv, sessionmaker):
    session, send, _ = fenv
    await send(text_update(UID, "/start"))
    await send(text_update(UID, t.MENU_FEEDBACK))  # до встречи: сразу отзыв о клубе
    assert session.last_text() == t.FB_CLUB_CATEGORY
    await send(callback_update(UID, "fbc:idea"))
    assert session.last_text() == t.FB_CLUB_TEXT
    await send(text_update(UID, "Сделайте встречу про венчур"))
    assert session.last_text() == t.FB_CLUB_THANKS
    await send(text_update(UID, t.MENU_FEEDBACK))
    await send(callback_update(UID, "fbc:bot"))
    await send(text_update(UID, "Не пришёл PDF"))
    rows = await _fb(sessionmaker)
    assert [(f.kind, f.category, f.comment) for f in rows] == [
        ("club", "idea", "Сделайте встречу про венчур"), ("club", "bot", "Не пришёл PDF")]


async def test_admin_feedback_export_and_stats(fenv, sessionmaker):
    session, send, past_event = fenv
    await _registered(sessionmaker)
    await past_event()
    await send(callback_update(UID, "fb:r:1:4"))
    await send(callback_update(UID, "fb:t:speaker"))
    await send(callback_update(UID, "fb:td"))
    await send(text_update(UID, "Отличный спикер"))
    await send(callback_update(UID, "fb:skip:next"))
    await send(callback_update(UID, "fb:club"))
    await send(callback_update(UID, "fbc:wish"))
    await send(text_update(UID, "=чаще встречи"))

    await send(text_update(ADMIN, "/stats 1"))
    out = session.last_text()
    assert "Отзывы о встрече" in out and "4★ — 1" in out and "Спикер: 1" in out and "Отзывов о клубе всего: 1" in out

    await send(text_update(ADMIN, "/feedback"))
    doc = session.params("SendDocument")[-1]["document"]
    rows = list(csv.reader(io.StringIO(doc.data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][:6] == ["kind", "event_id", "event_title", "full_name", "username", "telegram_id"]
    event_row, club_row = rows[1], rows[2]
    assert event_row[0] == "о встрече" and event_row[3] == "Иванов Иван Иванович" and event_row[6] == "4"
    assert event_row[8] == "Спикер" and event_row[10] == "Отличный спикер"
    assert club_row[0] == "о клубе" and club_row[9] == "Пожелание" and club_row[10] == "'=чаще встречи"


async def test_delete_me_removes_feedback(fenv, sessionmaker):
    session, send, past_event = fenv
    await _registered(sessionmaker)
    await past_event()
    await send(callback_update(UID, "fb:r:1:5"))
    await send(callback_update(UID, "fb:club"))
    await send(callback_update(UID, "fbc:other"))
    await send(text_update(UID, "Личное"))
    assert len(await _fb(sessionmaker)) == 2
    await reg.delete_user(sessionmaker, UID)
    assert await _fb(sessionmaker) == []


# ---------- поддержка ----------

async def test_support_question_and_reply(fenv, sessionmaker):
    session, send, _ = fenv
    await send(text_update(UID, t.MENU_SUPPORT))
    await send(text_update(UID, "Где парковка?"))
    assert session.last_text() == t.SUPPORT_UNAVAILABLE  # аккаунт поддержки ещё не нажимал /start

    async with sessionmaker() as s, s.begin():
        s.add(User(id=SUPPORT, username="Meine_Nika", interests=[]))
    session.reset()
    await send(text_update(UID, t.MENU_SUPPORT))
    assert session.last_text() == t.SUPPORT_ASK
    await send(text_update(UID, t.MENU_TICKET))  # кнопка внизу не уходит в поддержку
    await send(text_update(UID, "Где парковка?"))
    to_support = [p for n, p in session.calls if n == "SendMessage" and p["chat_id"] == SUPPORT]
    assert len(to_support) == 1 and "Где парковка?" in to_support[0]["text"] and f"#id{UID}" in to_support[0]["text"]
    assert session.last_text() == t.SUPPORT_SENT

    # ответ свайпом: бот пересылает пользователю
    from tests.fake_telegram import BOT_ID, _upd
    from aiogram.types import Update

    reply = Update.model_validate({
        "update_id": next(_upd),
        "message": {
            "message_id": 5, "date": 0, "chat": {"id": SUPPORT, "type": "private"},
            "from": {"id": SUPPORT, "is_bot": False, "first_name": "Ника", "username": "meine_nika"},
            "text": "Во дворе, въезд с Зубовского",
            "reply_to_message": {
                "message_id": 4, "date": 0, "chat": {"id": SUPPORT, "type": "private"},
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "Vitalis"},
                "text": to_support[0]["text"],
            },
        },
    })
    session.reset()
    await send(reply)
    to_user = [p for n, p in session.calls if n == "SendMessage" and p["chat_id"] == UID]
    assert to_user[0]["text"] == f"{t.SUPPORT_REPLY_TITLE}\n\nВо дворе, въезд с Зубовского"
    assert session.last_text() == t.SUPPORT_REPLY_SENT


# ---------- /event_new и ends_at ----------

async def test_event_new_wizard(fenv, sessionmaker):
    session, send, _ = fenv
    year = datetime.now(MSK).year + 1
    await send(text_update(ADMIN, "/event_new"))
    for answer in (
        "Встреча с Анной Ивановой",
        "Анна Иванова, CEO компании «Х»",
        "10.12.2020 18:00",  # в прошлом: переспросит
        f"10.12.{year} 18:00",
        "17:00",  # раньше начала: переспросит
        "21:00",
        "Кампус, Москва, ул. Пример, 1",
        "-",
        "40",
        "-",
    ):
        await send(text_update(ADMIN, answer))
    texts = session.texts()
    assert "Это время уже прошло" in texts and "Окончание должно быть позже начала" in texts
    assert "Проверь встречу" in session.last_text() and "18:00–21:00" in session.last_text()
    await send(callback_update(ADMIN, "en:create"))
    await send(callback_update(ADMIN, "en:create"))  # повторный тап не создаёт вторую встречу
    async with sessionmaker() as s:
        events = (await s.execute(select(Event).where(Event.id != 1))).scalars().all()
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Встреча с Анной Ивановой" and ev.seats == 40 and ev.registration_limit == 50
    assert ev.starts_at == datetime(year, 12, 10, 18, 0, tzinfo=MSK) and ev.ends_at == datetime(year, 12, 10, 21, 0, tzinfo=MSK)
    assert ev.venue_url is None and ev.is_open


async def test_event_set_ends_at(fenv, sessionmaker):
    session, send, _ = fenv
    await send(text_update(ADMIN, "/event_set ends_at 20:30"))
    async with sessionmaker() as s:
        ev = await s.get(Event, 1)
    assert ev.ends_at == datetime(2026, 11, 19, 20, 30, tzinfo=MSK)
    assert "опрос после встречи: 19.11 21:00" in session.last_text()
