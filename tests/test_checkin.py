"""Чекин: доступ по initData и роли, приглашения, скан QR, поиск, отметка, счётчик."""
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text, update

from bot.db.models import Checker
from bot.handlers import admin
from bot.services import registration as reg
from bot.services import tickets
from bot.services.delivery import Delivery
from bot.webapp.server import build_webapp
from tests import test_bot_flow
from tests.factories import add_users, profile
from tests.fake_telegram import make_bot, text_update

BOT_TOKEN = "123456:" + "A" * 35  # тот же, что у поддельного бота
ADMIN = 42
CHECKER = 555
STRANGER = 556
BEFORE = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def init_data(user_id: int, token: str = BOT_TOKEN, age: int = 0) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest_asyncio.fixture
async def cenv(sessionmaker):
    from bot.app import build_dispatcher

    admin.ADMIN_IDS.add(ADMIN)
    bot, session = make_bot()
    delivery = Delivery(bot, sessionmaker)
    if "dp" not in test_bot_flow._DP:
        test_bot_flow._DP["dp"] = build_dispatcher(sessionmaker, delivery)
    dp = test_bot_flow._DP["dp"]
    dp["sm"], dp["delivery"] = sessionmaker, delivery

    async def send(update_):
        await dp.feed_update(bot, update_)

    client = TestClient(TestServer(build_webapp(sessionmaker, BOT_TOKEN, lambda: admin.ADMIN_IDS)))
    await client.start_server()

    async def call(path, user_id=CHECKER, body=None, auth=None):
        headers = {"Authorization": auth if auth is not None else "tma " + init_data(user_id)}
        resp = await client.post(f"/api/checkin/{path}", json=body or {}, headers=headers)
        return resp.status, await resp.json()

    yield session, send, call, client
    await client.close()
    admin.ADMIN_IDS.discard(ADMIN)


async def _guests(sm):
    """Три зарегистрированных гостя и один отменивший."""
    names = {1: "Иванов Иван Иванович", 2: "Ёлкина Анна", 3: "Петров Пётр", 4: "Сидоров Сидор"}
    for uid, name in names.items():
        await reg.complete_registration(sm, uid, 1, profile(name=name), None, BEFORE)
    await reg.cancel(sm, 4, 1, BEFORE)


async def _invite_checker(session, send, user_id=CHECKER) -> str:
    await send(text_update(ADMIN, "/checkers"))
    link = re.search(r"start=(chk_[A-Za-z0-9_-]+)", session.last_text()).group(1)
    await send(text_update(user_id, f"/start {link}"))
    return link


async def test_access_requires_signed_init_data_and_role(cenv, sessionmaker):
    session, send, call, client = cenv
    assert (await call("me", auth=""))[0] == 401
    assert (await call("me", auth="tma " + init_data(CHECKER, token="999:" + "B" * 35)))[0] == 401  # чужая подпись
    assert (await call("me", auth="tma " + init_data(CHECKER, age=2 * 86400)))[0] == 401  # устаревший initData
    status, body = await call("me", user_id=STRANGER)
    assert status == 403 and body == {"error": "no_access"}
    status, body = await call("me", user_id=ADMIN)  # админ видит чекин без приглашения
    assert status == 200 and body["event"]["title"] == "Встреча с Семёном Покацким"
    page = await client.get("/checkin")
    assert page.status == 200 and "showScanQrPopup" in await page.text()


async def test_invite_is_one_time_and_expires(cenv, sessionmaker):
    session, send, call, _ = cenv
    link = await _invite_checker(session, send)
    assert "ты проверяющий" in session.last_text()
    assert (await call("me"))[0] == 200
    await send(text_update(CHECKER, f"/start {link}"))  # тот же человек открыл ссылку ещё раз: доступ остаётся
    assert "ты проверяющий" in session.last_text()
    await send(text_update(STRANGER, f"/start {link}"))  # чужой по той же ссылке не проходит
    assert "уже использована" in session.last_text()
    assert (await call("me", user_id=STRANGER))[0] == 403
    await send(text_update(STRANGER, "/start chk_nonexistent"))
    assert "недействительна" in session.last_text()

    # ссылка не стала источником регистрации
    from bot.db.models import User

    async with sessionmaker() as s:
        assert (await s.get(User, CHECKER)).source is None

    # после встречи доступ отключается сам
    async with sessionmaker() as s, s.begin():
        await s.execute(update(Checker).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    assert (await call("me"))[0] == 403


async def test_scan_results(cenv, sessionmaker):
    session, send, call, _ = cenv
    await _guests(sessionmaker)
    await _invite_checker(session, send)

    good = tickets.make_payload(1, 1)
    status, r = await call("scan", body={"payload": good})
    assert status == 200 and r["status"] == "ok" and r["name"] == "Иванов Иван Иванович" and r["ticket_no"] == 1
    assert r["came"] == 1 and r["total"] == 3
    assert not {"phone", "email", "university", "user_id"} & set(r)  # только ФИО и номер

    status, r = await call("scan", body={"payload": good})
    assert r["status"] == "already" and re.fullmatch(r"\d\d:\d\d", r["checked_at"]) and r["came"] == 1

    forged = good[:-1] + ("A" if good[-1] != "A" else "B")
    assert (await call("scan", body={"payload": forged}))[1]["status"] == "invalid"
    assert (await call("scan", body={"payload": "https://example.com"}))[1]["status"] == "invalid"
    assert (await call("scan", body={"payload": tickets.make_payload(99, 1)}))[1]["status"] == "other_event"
    assert (await call("scan", body={"payload": tickets.make_payload(1, 4)}))[1]["status"] == "cancelled"


async def test_search_mark_unmark_and_stats(cenv, sessionmaker):
    session, send, call, _ = cenv
    await _guests(sessionmaker)
    await _invite_checker(session, send)

    items = (await call("search", body={"q": "елкина"}))[1]["items"]  # ё = е
    assert [i["name"] for i in items] == ["Ёлкина Анна"]
    assert not {"phone", "email"} & set(items[0])
    assert [i["name"] for i in (await call("search", body={"q": "3"}))[1]["items"]] == ["Петров Пётр"]
    assert (await call("search", body={"q": "Сидоров"}))[1]["items"] == []  # отменивший не находится
    assert (await call("search", body={"q": "%"}))[1]["items"] == []

    anna = items[0]["id"]
    status, r = await call("mark", body={"id": anna})
    assert r["status"] == "ok" and r["came"] == 1
    assert (await call("mark", body={"id": anna}))[1]["status"] == "already"
    assert (await call("search", body={"q": "Ёлкина"}))[1]["items"][0]["checked_at"]
    status, r = await call("unmark", body={"id": anna})
    assert r["status"] == "unmarked" and r["came"] == 0
    assert (await call("mark", body={"id": "x"}))[0] == 400

    await call("mark", body={"id": anna})
    await send(text_update(ADMIN, "/stats"))
    assert "Пришло: 1 из 3" in session.last_text()
    await send(text_update(ADMIN, "/list"))
    assert "Ёлкина Анна — билет №2 ✓" in session.last_text() and "пришли: 1" in session.last_text()


async def test_checkin_command_only_for_staff(cenv, sessionmaker):
    session, send, _, _ = cenv
    await send(text_update(STRANGER, "/checkin"))
    assert not any("Чекин" in x for x in session.texts())
    await _invite_checker(session, send)
    await send(text_update(CHECKER, "/checkin"))
    assert "Чекин: Встреча с Семёном Покацким" in session.last_text() or "WEBAPP_URL" in session.last_text()
