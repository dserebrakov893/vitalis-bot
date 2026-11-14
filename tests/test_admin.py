"""Админка: доступ, /stats, /export (Excel), /list, /link, /broadcast, /event_set."""
import csv
import io

import pytest_asyncio
from sqlalchemy import select

from bot import texts as t
from bot.db.models import Event
from bot.handlers import admin
from bot.services import admin as adm
from bot.services import registration as reg
from bot.services.delivery import Delivery
from tests import test_bot_flow
from tests.factories import add_users, profile
from tests.fake_telegram import callback_update, make_bot, text_update
from tests.test_bot_flow import fill_form
from tests.test_registration import BEFORE

ADMIN = 42


@pytest_asyncio.fixture
async def aenv(sessionmaker):
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

    yield session, send
    admin.ADMIN_IDS.discard(ADMIN)


async def _populate(sm):
    names = ["Яковлев Ян", "Ёлкина Анна", "Борисов Борис Борисович", "Елисеев Егор", "=HYPERLINK(1) Злой"]
    for i, name in enumerate(names, start=1):
        p = profile(name=name, newsletter=i % 2 == 0)
        await reg.complete_registration(sm, i, 1, p, ["insta", "tg", None, "insta", "x"][i - 1], BEFORE)
    await reg.cancel(sm, 3, 1, BEFORE)


async def test_non_admin_has_no_access(aenv):
    session, send = aenv
    for cmd in ("/stats", "/export", "/list", "/broadcast", "/event"):
        await send(text_update(7, cmd))
    assert not any("Зарегистрировано" in x for x in session.texts())
    assert session.names().count("SendDocument") == 0


async def test_stats(aenv, sessionmaker):
    session, send = aenv
    await _populate(sessionmaker)
    await send(text_update(ADMIN, "/stats"))
    out = session.last_text()
    assert "Зарегистрировано: 4 из 75" in out and "Отмены: 1" in out and "Лист ожидания: 0" in out
    assert "insta: 2" in out and "Сеченовский: 4" in out and "Студент: 4" in out
    assert "Медтех и стартапы: 4" in out and "Согласие на рассылку: 2 из 4" in out


async def test_source_from_start_param_in_stats(aenv, sessionmaker):
    """Ручная приёмка, шаг 2: источник test виден в /stats."""
    session, send = aenv
    await send(text_update(501, "/start test"))
    await fill_form(send, session, 501)
    await send(callback_update(501, "f:news:no"))
    await send(text_update(ADMIN, "/stats"))
    assert "test: 1" in session.last_text()


async def test_export_opens_in_excel(aenv, sessionmaker):
    session, send = aenv
    await _populate(sessionmaker)
    await send(text_update(ADMIN, "/export"))
    doc = session.params("SendDocument")[0]["document"]
    assert doc.filename.endswith(".csv")
    raw = doc.data
    assert raw.startswith("﻿".encode("utf-8"))  # BOM: Excel распознаёт UTF-8
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][:6] == ["event_id", "ticket_no", "state", "full_name", "phone", "email"]
    assert len(rows) == 6
    names = {r[3] for r in rows[1:]}
    assert "Ёлкина Анна" in names
    assert "'=HYPERLINK(1) Злой" in names  # формулы обезврежены
    assert all(r[4] == "+79990000000" for r in rows[1:])


async def test_guard_list_sorted(aenv, sessionmaker):
    session, send = aenv
    await _populate(sessionmaker)
    await send(text_update(ADMIN, "/list"))
    out = session.last_text()
    body = out.split("\n")[1:]
    assert out.startswith("Встреча с Семёном Покацким") and "Всего: 4" in out
    order = [line.split(". ", 1)[1].split(" — ")[0] for line in body]
    assert order == ["=HYPERLINK(1) Злой", "Елисеев Егор", "Ёлкина Анна", "Яковлев Ян"]
    assert "Борисов" not in out  # отменённых нет


def test_chunking_long_list():
    lines = [f"{i}. Константинопольский Константин Константинович — билет №{i}" for i in range(1, 301)]
    chunks = adm.chunk_lines("header", lines)
    assert len(chunks) > 1 and all(len(c) <= 3800 for c in chunks)
    assert sum(c.count("билет №") for c in chunks) == 300


async def test_link(aenv):
    session, send = aenv
    await send(text_update(ADMIN, "/link insta_story"))
    assert session.last_text() == "https://t.me/vitalis_business_club_bot?start=insta_story"
    await send(text_update(ADMIN, "/link плохой код"))
    assert session.last_text().startswith("Формат")


async def test_broadcast_modes(aenv, sessionmaker):
    session, send = aenv
    await _populate(sessionmaker)
    await send(text_update(ADMIN, "/broadcast"))
    await send(callback_update(ADMIN, "bc:news"))
    await send(text_update(ADMIN, "Привет, клуб!"))
    assert "Получателей: 2." in session.last_text()
    session.reset()
    await send(callback_update(ADMIN, "bc:send"))
    import asyncio

    for _ in range(50):
        if any("Рассылка завершена" in x for x in session.texts()):
            break
        await asyncio.sleep(0.05)
    sent = [p for n, p in session.calls if n == "SendMessage" and p["chat_id"] != ADMIN]
    assert sorted(p["chat_id"] for p in sent) == [2, 4]
    assert all("/unsubscribe" in p["text"] for p in sent)
    assert "доставлено 2 из 2" in session.texts()[-1]

    session.reset()
    await send(callback_update(ADMIN, "bc:send"))  # повторный тап ничего не шлёт
    assert not [p for n, p in session.calls if n == "SendMessage"]

    await send(text_update(ADMIN, "/broadcast"))
    await send(callback_update(ADMIN, "bc:org"))
    await send(text_update(ADMIN, "Вход со двора"))
    assert "Получателей: 4." in session.last_text()
    await send(callback_update(ADMIN, "bc:cancel"))
    assert session.last_text() == "Рассылка отменена."


async def test_event_set(aenv, sessionmaker):
    session, send = aenv
    await add_users(sessionmaker, range(100, 103))
    for uid in range(100, 103):
        await reg.register(sessionmaker, uid, 1, None)
    await send(text_update(ADMIN, "/event_set registration_limit 2"))
    await send(text_update(ADMIN, "/event_set is_open nope"))
    assert session.last_text() == "is_open: true или false"
    await send(text_update(ADMIN, "/event_set color red"))
    assert session.last_text().startswith("Поле нельзя менять")
    await send(text_update(ADMIN, "/event_set venue_text Новый адрес, д. 1"))
    async with sessionmaker() as s:
        ev = await s.get(Event, 1)
    assert ev.registration_limit == 2 and ev.venue_text == "Новый адрес, д. 1" and ev.shared_widget_id is None

    # увеличение лимита отдаёт места листу ожидания и шлёт билет
    async with sessionmaker() as s:
        assert await reg.count_registered(s, 1) == 3  # лимит уменьшили, но регистрации не трогаем
    await add_users(sessionmaker, [103])
    await reg.register(sessionmaker, 103, 1, None)
    session.reset()
    await send(text_update(ADMIN, "/event_set registration_limit 4"))
    assert any(p["chat_id"] == 103 and p["text"] == t.PROMOTED for n, p in session.calls if n == "SendMessage")

    await send(text_update(ADMIN, "/event"))
    assert "registration_limit: 4" in session.last_text()


async def test_command_during_form_is_not_an_answer(aenv):
    session, send = aenv
    await send(text_update(501, "/start"))
    await send(callback_update(501, "go:1"))
    await send(text_update(501, "/about"))
    assert "hello@justbusinessdarling.com" in session.last_text()
    await send(text_update(501, "Иванов Иван"))
    assert session.last_text() == t.ASK_PHONE
