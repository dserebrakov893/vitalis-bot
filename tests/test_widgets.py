"""Виджеты: бюджет 4 КБ, логика JS (в Node), отправка, запросы, fallback."""
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from bot import texts as t
from bot.app import build_dispatcher
from bot.db.models import Event, Registration, User
from bot.formatting import calendar_url_short
from bot.services.delivery import Delivery
from bot.services.pdf_ticket import render_ticket_pdf
from bot.widgets import render
from bot.widgets.service import WidgetService
from tests.fake_telegram import callback_update, make_bot, raw_update, text_update
from tests.test_bot_flow import fill_form

UID = 601
LONG_VENUE = (
    "Совместный цифровой кампус «Школы 21» и Сеченовского университета, "
    "Москва, Зубовский бульвар, д. 37, стр. 1, м. «Парк культуры»"
)


def _event(**kw):
    data = dict(
        id=1,
        title="Встреча с Семёном Покацким",
        speaker="Семён Покацкий, сооснователь и директор по развитию бизнеса биотехнологической компании «Рапид Био»",
        starts_at=datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc),
        venue_text=LONG_VENUE,
        seats=60,
        registration_limit=75,
        is_open=True,
    )
    data.update(kw)
    return Event(**data)


# ---------- бюджет 4 КБ ----------

@pytest.mark.parametrize(
    "event",
    [
        _event(),
        # с запасом: длинные название, спикер и адрес
        _event(title="Встреча с Константином Константинопольским: как запустить медтех-стартап из лаборатории",
               speaker="со-основатель и генеральный директор компании Rapid Bio, кандидат медицинских наук",
               venue_text=LONG_VENUE + ", вход со стороны Зубовского бульвара, третий этаж, большой зал"),
    ],
)
def test_widget_html_fits_4kb(event):
    import zlib

    for html in (render.event_card_html(event), render.ticket_html(event)):
        assert len(zlib.compress(html.encode(), 9)) <= 4096
        assert len(html.encode()) <= 32768


def test_every_template_fits_4kb_raw():
    """Каждый шаблон как есть (с плейсхолдерами) тоже проходит по бюджету."""
    import zlib

    for path in Path(render.TEMPLATES).glob("*.html"):
        html = render._template(path.name)
        assert len(zlib.compress(html.encode(), 9)) <= 4096, path.name


def test_state_and_links_within_limits():
    user = User(id=1, full_name="Ж" * 100)
    reg = Registration(id=1, ticket_no=75, feedback_score=5)
    for status in render.TICKET_STATUSES:
        assert len(render.ticket_chat_data(user, reg, status).encode()) <= 1024
    assert len(render.card_public_data(75, True).encode()) <= 1024
    cal = calendar_url_short(_event().starts_at)
    assert cal.startswith("https://") and len(cal) <= 256
    assert len(render.card_caption(_event(), 75)) <= 1024
    assert "Не видишь карточку? Обнови Telegram или нажми /reg" in render.card_caption(_event(), 3)
    assert "Не видишь карточку? Обнови Telegram или нажми /reg" in render.ticket_caption(_event(), reg)


# ---------- JS виджетов ----------

NODE = shutil.which("node")
HARNESS = Path(__file__).parent / "widget_harness.js"


def run_widget(html, tw=None, actions=(), reject=None):
    payload = {"html": html, "tw": tw or {}, "actions": list(actions), "reject": reject}
    out = subprocess.run([NODE, str(HARNESS)], input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_card_widget_js():
    future = _event(starts_at=datetime.now(timezone.utc) + timedelta(days=2, hours=3))
    html = render.event_card_html(future)
    res = run_widget(html, {"publicData": json.dumps({"r": 1, "l": 12, "o": 1})},
                     actions=[["click", "g"], ["public", json.dumps({"r": 0, "l": 99, "o": 1})],
                              ["public", json.dumps({"r": 5, "l": 0, "o": 1})]])
    first, clicked, stale, full = res["snaps"]
    assert first["l"]["t"] == "12" and first["g"]["t"] == "Иду" and first["c"]["t"].startswith("2 д")
    assert res["watched"] and res["requests"] == ["seats:1", "reg:1"] and clicked["s"]["t"] == "Анкета ниже в чате"
    assert stale["l"]["t"] == "12"  # старая ревизия игнорируется
    assert full["l"]["t"] == "0" and full["g"]["t"] == "Встать в лист ожидания"

    past = render.event_card_html(_event(starts_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    closed = run_widget(past, {"publicData": json.dumps({"r": 1, "l": 5, "o": 1})})["snaps"][0]
    assert closed["g"]["d"] is True and closed["g"]["t"] == "Регистрация закрыта" and closed["c"]["t"] == "идёт"

    err = run_widget(html, {}, actions=[["click", "g"]], reject="TIMEOUT")["snaps"][-1]
    assert err["s"]["t"] == "Бот не ответил, нажми /reg"


@pytest.mark.skipif(NODE is None, reason="node не установлен")
def test_ticket_widget_js():
    html = render.ticket_html(_event())
    base = {"r": 1, "n": "Иванов Иван Иванович", "no": 7}
    res = run_widget(html, {"chatData": json.dumps({**base, "s": "registered"})},
                     actions=[["click", "cal"], ["click", "cx"],
                              ["chat", json.dumps({**base, "r": 2, "s": "soon"})],
                              ["chat", json.dumps({**base, "r": 3, "s": "live"})],
                              ["chat", json.dumps({**base, "r": 4, "s": "done"})],
                              ["star", 3]])
    reg, cal, cx, soon, live, done, rated = res["snaps"]
    assert reg["n"]["t"] == "Иванов Иван Иванович" and reg["no"]["t"] == "№ 7" and reg["st"]["t"] == "Ты в списке"
    assert res["links"] == [calendar_url_short(_event().starts_at)]
    assert cx["s"]["t"] == "Подтверди отмену в чате"
    assert soon["st"]["t"] == "Начинаем через 2 часа" and not soon["cx"]["h"]
    assert live["st"]["t"] == "Мы начали, заходи" and live["cx"]["h"] and live["cal"]["h"]
    assert done["st"]["t"] == "Оцени встречу" and done["rt"]["stars"] == "☆☆☆☆☆"
    assert res["requests"] == ["cancel", "rate:4"] and rated["rt"]["stars"] == "★★★★☆"

    cancelled = run_widget(html, {"chatData": json.dumps({**base, "s": "cancelled"})})["snaps"][0]
    assert cancelled["st"]["t"] == "Билет аннулирован" and cancelled["cx"]["h"]


# ---------- бот с виджетами ----------

_DP = {}


@pytest_asyncio.fixture
async def wenv(sessionmaker):
    bot, session = make_bot()
    widgets = WidgetService(bot, sessionmaker, enabled=True, autoflush=False)
    delivery = Delivery(bot, sessionmaker, widgets=widgets, pdf_renderer=render_ticket_pdf)
    from tests import test_bot_flow

    if "dp" not in test_bot_flow._DP:
        test_bot_flow._DP["dp"] = build_dispatcher(sessionmaker, delivery)
    dp = test_bot_flow._DP["dp"]
    dp["sm"] = sessionmaker
    dp["delivery"] = delivery

    async def send(update):
        await dp.feed_update(bot, update)

    return session, send, widgets


def widget_query(uid, data, *, message_id=None, widget_id=None, qid="q1"):
    q = {"id": qid, "from": {"id": uid, "is_bot": False, "first_name": "Иван"},
         "chat": {"id": uid, "type": "private"}, "chat_instance": "1", "type": {"type": "data"}, "data": data}
    if message_id:
        q["message_id"] = message_id
    if widget_id:
        q["widget_id"] = widget_id
    return raw_update({"widget_query": q})


async def test_widget_flow(wenv, sessionmaker):
    session, send, widgets = wenv
    await send(text_update(UID, "/start src1"))
    sw = session.params("SendWidget")
    assert len(sw) == 1 and sw[0]["is_shared"] and sw[0]["allow_queries"]
    assert json.loads(sw[0]["public_data"])["l"] == 75
    assert "/reg" in sw[0]["caption"]
    edit = session.params("EditSharedWidgetData")
    assert edit and edit[0]["cache_time"] == 10
    async with sessionmaker() as s:
        widget_id = (await s.get(Event, 1)).shared_widget_id
    assert widget_id

    # второму человеку уходит тот же shared-виджет
    session.reset()
    await send(text_update(UID + 1, "/start"))
    assert session.params("SendSharedWidget")[0]["widget_id"] == widget_id and not session.params("SendWidget")

    # «Иду» в виджете: бот отвечает на запрос и начинает анкету сообщениями
    session.reset()
    await send(widget_query(UID, "reg", widget_id=widget_id))
    assert session.params("AnswerWidgetQuery")[0] == {"widget_query_id": "q1", "data": '{"ok":1}'}
    assert session.last_text() == t.ASK_FULL_NAME
    await send(callback_update(UID, "f:full_name:back"))
    await fill_form(send, session, UID)
    session.reset()
    await send(callback_update(UID, "f:news:yes"))
    tw = session.params("SendWidget")
    assert len(tw) == 1 and tw[0]["allow_queries"] and not tw[0].get("is_shared")
    state = json.loads(tw[0]["chat_data"])
    assert state["n"] == "Иванов Иван Иванович" and state["no"] == 1 and state["s"] == "registered"
    assert session.names().count("SendDocument") == 1
    assert widgets._dirty == {1}
    session.reset()
    await widgets.flush_cards()
    assert json.loads(session.params("EditSharedWidgetData")[0]["public_data"])["l"] == 74

    async with sessionmaker() as s:
        reg = (await s.execute(select(Registration))).scalar_one()
    assert reg.ticket_widget_msg_id and reg.ticket_widget_status == "registered" and reg.source == "src1"

    # «Не смогу прийти» в виджете → подтверждение в чате → статус «cancelled» в виджете
    session.reset()
    await send(widget_query(UID, "cancel", message_id=reg.ticket_widget_msg_id, qid="q2"))
    assert session.last_text() == t.CANCEL_CONFIRM
    session.reset()
    await send(callback_update(UID, "cx:yes:1"))
    edits = session.params("EditMessageWidgetData")
    assert edits[0]["message_id"] == reg.ticket_widget_msg_id and json.loads(edits[0]["chat_data"])["s"] == "cancelled"


async def test_static_card_when_shared_not_allowed(wenv, sessionmaker):
    """Прод может не пускать shared-виджеты: тогда обычная карточка-виджет, счётчик по запросу seats."""
    from aiogram.exceptions import TelegramBadRequest

    session, send, widgets = wenv
    real = session.make_request

    async def no_shared(bot, method, timeout=None):
        if type(method).__name__ == "SendWidget" and method.is_shared:
            session.calls.append(("SendWidget", method.model_dump(exclude_none=True)))
            raise TelegramBadRequest(method=method, message="Bad Request: INPUT_MEDIA_WIDGET_NOT_ALLOWED")
        return await real(bot, method, timeout)

    session.make_request = no_shared
    await send(text_update(UID, "/start"))
    sent = session.params("SendWidget")
    assert [bool(p.get("is_shared")) for p in sent] == [True, False]  # попытка shared, затем обычный
    assert sent[1]["allow_queries"] and json.loads(sent[1]["public_data"])["l"] == 75
    assert not any("Осталось мест" in x for x in session.texts())  # текстовый fallback не понадобился
    assert widgets.shared_allowed is False

    session.reset()
    await send(text_update(UID + 1, "/start"))  # второй раз shared уже не пробуем
    assert [bool(p.get("is_shared")) for p in session.params("SendWidget")] == [False]

    session.reset()
    await send(widget_query(UID, "seats:1", qid="s1"))
    assert json.loads(session.params("AnswerWidgetQuery")[0]["data"])["l"] == 75
    await send(widget_query(UID, "reg:1", qid="s2"))
    assert session.last_text() == t.ASK_FULL_NAME


async def test_rating_from_widget(wenv, sessionmaker):
    session, send, widgets = wenv
    await send(text_update(UID, "/start"))
    await send(widget_query(UID, "reg"))
    await fill_form(send, session, UID)
    await send(callback_update(UID, "f:news:no"))
    async with sessionmaker() as s:
        reg = (await s.execute(select(Registration))).scalar_one()
    await widgets.set_ticket_status(reg, "done")
    session.reset()
    await send(widget_query(UID, "rate:5", message_id=reg.ticket_widget_msg_id, qid="q9"))
    assert session.params("AnswerWidgetQuery")[0]["data"] == '{"ok":1}'
    chat = json.loads(session.params("EditMessageWidgetData")[0]["chat_data"])
    assert chat["s"] == "done" and chat["sc"] == 5
    async with sessionmaker() as s:
        assert (await s.get(Registration, reg.id)).feedback_score == 5

    session.reset()
    await send(widget_query(UID, "rate:9", qid="q10"))
    await send(widget_query(UID, "x" * 10, qid="q11"))
    assert [p["data"] for p in session.params("AnswerWidgetQuery")] == ['{"e":"unknown"}', '{"e":"unknown"}']


async def test_wallet_and_forward_queries_are_refused(wenv):
    session, send, _ = wenv
    upd = raw_update({"shared_widget_query": {"id": "s1", "from": {"id": UID, "is_bot": False, "first_name": "A"},
                                              "type": {"type": "data"}, "data": "reg", "widget_id": "1"}})
    await send(upd)
    q = widget_query(UID, "reg", qid="w1")
    q.model_extra["widget_query"]["type"] = {"type": "sign_data"}
    await send(q)
    assert [p["data"] for p in session.params("AnswerWidgetQuery")] == ['{"e":"unsupported"}'] * 2
    assert session.texts() == []


async def test_widget_errors_fall_back(wenv, sessionmaker):
    session, send, _ = wenv
    session.fail = {"SendWidget", "SendSharedWidget"}
    await send(text_update(UID, "/start"))
    assert "Осталось мест: 75" in session.last_text()  # fallback-карточка
    await send(callback_update(UID, "go:1"))
    await fill_form(send, session, UID)
    session.reset()
    await send(callback_update(UID, "f:news:no"))
    assert any("Vitalis · билет №1" in x for x in session.texts())
    assert session.names().count("SendDocument") == 1


async def test_reg_command_never_uses_widgets(wenv):
    session, send, _ = wenv
    await send(text_update(UID, "/reg"))
    await send(callback_update(UID, "go:1"))
    await fill_form(send, session, UID)
    await send(callback_update(UID, "f:news:no"))
    assert not session.params("SendWidget") and not session.params("SendSharedWidget")
    assert any("Vitalis · билет №1" in x for x in session.texts())
    session.reset()
    await send(text_update(UID, "/ticket"))
    assert not session.params("SendWidget") and "Vitalis · билет №1" in session.texts()[0]


async def test_kill_switch(wenv):
    session, send, widgets = wenv
    widgets.enabled = False
    await send(text_update(UID, "/start"))
    assert not session.params("SendWidget") and "Осталось мест: 75" in session.last_text()


async def test_new_card_after_widget_reset(wenv, engine, sessionmaker):
    session, send, _ = wenv
    await send(text_update(UID, "/start"))
    async with engine.begin() as c:
        await c.execute(text("UPDATE events SET shared_widget_id=NULL WHERE id=1"))
    session.reset()
    await send(text_update(UID + 1, "/start"))
    assert len(session.params("SendWidget")) == 1  # создан новый shared-виджет
