"""Сквозной сценарий на поддельном Telegram: от /start test до билета, отмена, удаление."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from bot import texts as t
from bot.app import build_dispatcher
from bot.db.models import Consent, Registration, User
from bot.services.delivery import Delivery
from bot.services.pdf_ticket import render_ticket_pdf
from tests.fake_telegram import callback_update, contact_update, make_bot, text_update

UID = 501


_DP = {}


@pytest_asyncio.fixture
async def env(sessionmaker):
    bot, session = make_bot()
    delivery = Delivery(bot, sessionmaker, pdf_renderer=render_ticket_pdf)
    # Роутеры aiogram подключаются к диспетчеру один раз, поэтому диспетчер общий, а зависимости свежие
    if "dp" not in _DP:
        _DP["dp"] = build_dispatcher(sessionmaker, delivery)
    dp = _DP["dp"]
    dp["sm"] = sessionmaker
    dp["delivery"] = delivery

    async def send(update):
        await dp.feed_update(bot, update)

    return bot, session, send, delivery


async def fill_form(send, session, uid=UID):
    await send(callback_update(uid, "go:1"))
    assert session.last_text() == t.ASK_FULL_NAME
    await send(text_update(uid, "Иванов Иван Иванович"))
    assert session.last_text() == t.ASK_PHONE
    await send(contact_update(uid, uid))
    assert session.last_text() == t.ASK_UNIVERSITY
    await send(callback_update(uid, "f:university:sech"))
    await send(callback_update(uid, "f:status:student"))
    await send(callback_update(uid, "f:interests:medtech"))
    await send(callback_update(uid, "f:interests:done"))
    await send(callback_update(uid, "f:pdn:adult"))
    assert session.last_text() == t.consent_news_html()


async def test_full_registration_flow(env, sessionmaker):
    bot, session, send, _ = env
    await send(text_update(UID, "/start test"))
    card = session.last_text()
    assert "<b>Встреча с Семёном Покацким</b>" in card and "Бизнес-клуб Vitalis" in card and "четверг, 19 ноября, 17:00" in card and "Осталось мест: 75" in card

    await send(callback_update(UID, "go:1"))
    assert session.last_text() == t.ASK_FULL_NAME
    # «Назад» с первого шага возвращает к карточке
    await send(callback_update(UID, "f:full_name:back"))
    assert "Осталось мест: 75" in session.last_text()

    await send(callback_update(UID, "go:1"))
    await send(text_update(UID, "Иван"))
    assert session.last_text() == t.BAD_FULL_NAME
    await send(text_update(UID, "Иванов Иван Иванович"))
    await send(contact_update(UID, 999))  # чужой контакт
    assert session.last_text() == t.BAD_PHONE
    await send(text_update(UID, "+79990000000"))  # ручной ввод не принимаем
    assert session.last_text() == t.BAD_PHONE
    await send(contact_update(UID, UID))
    assert session.last_text() == t.ASK_UNIVERSITY  # сразу вуз, без почты
    await send(callback_update(UID, "f:university:other"))
    assert session.last_text() == t.ASK_UNIVERSITY_OTHER
    await send(callback_update(UID, "f:university_other:back"))
    assert session.last_text() == t.ASK_UNIVERSITY
    await send(callback_update(UID, "f:university:other"))
    await send(text_update(UID, "Казанский ГМУ"))
    assert session.last_text() == t.ASK_STATUS
    await send(text_update(UID, "просто текст"))
    assert session.last_text() == t.USE_BUTTONS
    await send(callback_update(UID, "f:status:student"))
    session.reset()
    await send(callback_update(UID, "f:interests:done"))  # без выбора нельзя
    assert ("AnswerCallbackQuery", {"callback_query_id": session.calls[0][1]["callback_query_id"],
                                    "text": t.NEED_INTEREST, "show_alert": True}) in session.calls
    await send(callback_update(UID, "f:interests:medtech"))
    await send(callback_update(UID, "f:interests:pharma_biotech"))
    await send(callback_update(UID, "f:interests:medtech"))  # снять галочку
    await send(callback_update(UID, "f:interests:done"))
    await send(callback_update(UID, "f:pdn:back"))  # назад к интересам
    assert session.last_text() == t.ASK_INTERESTS
    await send(callback_update(UID, "f:interests:done"))
    await send(callback_update(UID, "f:pdn:17_with_parent"))
    session.reset()
    await send(callback_update(UID, "f:news:no"))
    texts = session.texts()
    assert texts[0].startswith("<b>Готово, Иван, ты в списке!</b>") and "Твой билет №1 ниже. Ещё мы продублировали его файлом." in texts[0]
    assert "Твой билет №1" in texts[1] and "Иванов Иван Иванович" in texts[1]
    docs = [p for n, p in session.calls if n == "SendDocument"]
    assert len(docs) == 1 and docs[0]["document"].filename == "Vitalis_bilet_1.pdf"
    assert docs[0]["document"].data.startswith(b"%PDF")

    # повторный тап по последней кнопке: второго билета нет
    session.reset()
    await send(callback_update(UID, "f:news:no"))
    assert session.texts() == []

    async with sessionmaker() as s:
        user = await s.get(User, UID)
        reg = (await s.execute(select(Registration))).scalar_one()
        consents = (await s.execute(select(Consent.type))).scalars().all()
        fsm = await s.execute(select(__import__("bot.db.models", fromlist=["FsmState"]).FsmState.data))
    assert user.source == "test" and reg.source == "test"
    assert user.university == "Казанский ГМУ" and user.interests == ["pharma_biotech"]
    assert user.project_stage is None and user.age_group == "17_with_parent"
    assert user.email is None and user.phone == "+79990000000"
    assert user.newsletter_opt_in is False and consents == ["pdn"]
    # после регистрации в FSM нет ПДн
    for data in fsm.scalars():
        assert "full_name" not in data and "phone" not in data and "email" not in data

    # /start теперь показывает билет, а не карточку
    session.reset()
    await send(text_update(UID, "/start"))
    assert "Твой билет №1" in session.last_text()

    # /ticket повторно присылает билет и PDF
    session.reset()
    await send(text_update(UID, "/ticket"))
    assert session.names().count("SendDocument") == 1 and "Твой билет №1" in session.texts()[0]


async def test_cancel_and_waitlist_promotion(env, sessionmaker, engine):
    from sqlalchemy import text

    bot, session, send, _ = env
    async with engine.begin() as c:
        await c.execute(text("UPDATE events SET registration_limit=1 WHERE id=1"))
    await send(text_update(UID, "/start"))
    await fill_form(send, session, UID)
    await send(callback_update(UID, "f:news:yes"))
    await send(text_update(UID + 1, "/start"))
    assert "Мест нет" in session.last_text()
    await fill_form(send, session, UID + 1)
    await send(callback_update(UID + 1, "f:news:no"))
    assert session.last_text() == t.waitlist_text(1)

    await send(text_update(UID, "/cancel"))
    assert session.last_text() == t.CANCEL_CONFIRM
    session.reset()
    await send(callback_update(UID, "cx:yes:1"))
    calls = session.calls
    texts = session.texts()
    assert t.CANCEL_DONE in texts
    promoted = [p for n, p in calls if n == "SendMessage" and p["chat_id"] == UID + 1]
    assert promoted[0]["text"] == t.PROMOTED and "Твой билет №2" in promoted[1]["text"]

    session.reset()
    await send(callback_update(UID, "cx:yes:1"))  # повторный тап
    assert session.texts() == [t.CANCEL_NOTHING]


async def test_delete_me(env, sessionmaker):
    bot, session, send, _ = env
    await send(text_update(UID, "/start"))
    await fill_form(send, session, UID)
    await send(callback_update(UID, "f:news:yes"))
    await send(text_update(UID, "/delete_me"))
    assert session.last_text() == t.DELETE_CONFIRM
    await send(callback_update(UID, "del:yes"))
    assert session.last_text() == t.DELETE_DONE
    async with sessionmaker() as s:
        assert await s.get(User, UID) is None
        assert (await s.execute(select(Registration))).first() is None


@pytest.mark.parametrize("cmd, expected", [("/unsubscribe", t.UNSUBSCRIBED)])
async def test_simple_commands(env, cmd, expected):
    _, session, send, _ = env
    await send(text_update(UID, cmd))
    assert session.last_text() == expected


async def test_club_and_about_are_disabled(env):
    _, session, send, _ = env
    for cmd in ("/club", "/about"):
        await send(text_update(UID, cmd))
        assert session.last_text() == t.MAIN_HINT
    assert [c for c, _ in t.COMMANDS] == ["start", "ticket", "cancel"]


async def test_bottom_keyboard(env, sessionmaker):
    _, session, send, _ = env

    def has_kb(params):
        rm = params.get("reply_markup") or {}
        return [[b["text"] for b in row] for row in rm.get("keyboard", [])] == [[t.MENU_EVENT, t.MENU_TICKET], [t.MENU_CANCEL]]

    await send(text_update(UID, "/start"))
    assert "DeleteMessage" not in session.names()
    first, card = session.params("SendMessage")[:2]
    assert first["text"] == t.CARD_LEAD and has_kb(first)  # кнопки внизу сразу с первым /start
    assert "Осталось мест: 75" in card["text"]

    await send(text_update(UID, "Ближайшая встреча"))  # старая подпись без эмодзи тоже работает
    assert "Осталось мест: 75" in session.last_text()
    session.reset()
    await send(text_update(UID, t.MENU_EVENT))
    assert "Осталось мест: 75" in session.last_text()
    await send(text_update(UID, t.MENU_TICKET))
    assert session.last_text() == t.NO_TICKET and has_kb(session.params("SendMessage")[-1])
    await send(text_update(UID, t.MENU_CANCEL))
    assert session.last_text() == t.CANCEL_NOTHING

    # кнопка внизу посреди анкеты не считается ответом на шаг; после телефона клавиатура возвращается
    await send(callback_update(UID, "go:1"))
    await send(text_update(UID, "Иванов Иван"))
    await send(contact_update(UID, UID))
    after_phone = [p for n, p in session.calls if n == "SendMessage" and p["text"] == t.PHONE_SAVED][0]
    assert has_kb(after_phone)
    await send(text_update(UID, t.MENU_TICKET))
    assert session.last_text() == t.NO_TICKET
    await send(callback_update(UID, "f:university:back"))  # назад с вуза ведёт к телефону
    assert session.last_text() == t.ASK_PHONE
    await send(contact_update(UID, UID))

    await send(callback_update(UID, "f:university:sech"))
    await send(callback_update(UID, "f:status:student"))
    await send(callback_update(UID, "f:interests:medtech"))
    await send(callback_update(UID, "f:interests:done"))
    await send(callback_update(UID, "f:pdn:adult"))
    session.reset()
    await send(callback_update(UID, "f:news:no"))
    assert has_kb(session.params("SendDocument")[0])  # PDF-билет ставит клавиатуру

    session.reset()
    await send(text_update(UID, t.MENU_TICKET))
    assert "Твой билет №1" in session.texts()[0] and has_kb(session.params("SendDocument")[0])
    await send(text_update(UID, t.MENU_CANCEL))
    assert session.last_text() == t.CANCEL_CONFIRM
