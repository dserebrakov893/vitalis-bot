from dataclasses import replace

from bot.config import Settings
from bot.services import mailer
from tests.fake_telegram import text_update


async def test_mail_disabled_without_smtp_host(caplog):
    cfg = Settings(smtp_host="")
    caplog.set_level("INFO")
    assert await mailer.send_ticket("a@b.ru", "Иванов Иван", b"%PDF", cfg=cfg) is False
    assert await mailer.send_newsletter("a@b.ru", "Тема", "Текст", cfg=cfg) is False
    assert "mail disabled" in caplog.text


async def test_mail_enabled_builds_message(monkeypatch):
    sent = []

    async def fake_send(msg, **kw):
        sent.append((msg, kw))

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    cfg = replace(Settings(), smtp_host="smtp.example.ru", smtp_port=465, smtp_from="club@example.ru")
    assert await mailer.send_ticket("a@b.ru", "Иванов Иван", b"%PDF-1", cfg=cfg)
    assert await mailer.send_newsletter("a@b.ru", "Новости", "Текст", cfg=cfg)
    ticket, news = sent[0][0], sent[1][0]
    assert sent[0][1]["use_tls"] is True
    assert [p.get_filename() for p in ticket.iter_attachments()] == ["Vitalis_bilet.pdf"]
    assert mailer.UNSUBSCRIBE_URL in news.get_content() and news["List-Unsubscribe"]


async def test_unsubscribe_link_from_email(sessionmaker):
    from bot import texts as t
    from tests.test_bot_flow import _DP
    from bot.app import build_dispatcher
    from bot.services.delivery import Delivery
    from tests.fake_telegram import make_bot

    bot, session = make_bot()
    delivery = Delivery(bot, sessionmaker)
    dp = _DP.get("dp") or build_dispatcher(sessionmaker, delivery)
    _DP["dp"] = dp
    dp["sm"], dp["delivery"] = sessionmaker, delivery
    await dp.feed_update(bot, text_update(5, "/start unsubscribe"))
    assert session.last_text() == t.UNSUBSCRIBED
