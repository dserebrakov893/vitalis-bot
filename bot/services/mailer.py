"""Почта (задел, сейчас выключена). Пустой SMTP_HOST = почта выключена, функции возвращают False."""
from __future__ import annotations

import logging
from email.message import EmailMessage

from bot.config import BOT_USERNAME, Settings, settings

log = logging.getLogger(__name__)

UNSUBSCRIBE_URL = f"https://t.me/{BOT_USERNAME}?start=unsubscribe"


def enabled(cfg: Settings | None = None) -> bool:
    return bool((cfg or settings).smtp_host)


async def _send(msg: EmailMessage, cfg: Settings) -> bool:
    import aiosmtplib

    try:
        await aiosmtplib.send(
            msg,
            hostname=cfg.smtp_host,
            port=cfg.smtp_port,
            username=cfg.smtp_user or None,
            password=cfg.smtp_password or None,
            use_tls=cfg.smtp_port == 465,
            start_tls=cfg.smtp_port != 465,
            timeout=30,
        )
        return True
    except Exception as e:  # noqa: BLE001 — почта не должна ронять бота
        log.warning("mail send failed: %r", e)  # адрес в лог не пишем
        return False


async def send_ticket(email: str, full_name: str, pdf_bytes: bytes, ticket_no: int | None = None,
                      cfg: Settings | None = None) -> bool:
    cfg = cfg or settings
    if not enabled(cfg):
        log.info("mail disabled")
        return False
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from
    msg["To"] = email
    msg["Subject"] = "Твой билет на встречу Vitalis"
    msg.set_content(
        f"{full_name}, привет!\n\nБилет на встречу клуба Vitalis во вложении. "
        "Покажи QR-код на входе.\n\nНапоминания придут в Telegram-бот.\n\nБилет сделан JBD · justbusinessdarling.com"
    )
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename=f"Vitalis_bilet_{ticket_no or ''}.pdf".replace("_.pdf", ".pdf"))
    return await _send(msg, cfg)


async def send_newsletter(email: str, subject: str, text: str, cfg: Settings | None = None) -> bool:
    """Одно письмо рассылки. Слать только тем, у кого newsletter_opt_in = true."""
    cfg = cfg or settings
    if not enabled(cfg):
        log.info("mail disabled")
        return False
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from
    msg["To"] = email
    msg["Subject"] = subject
    msg["List-Unsubscribe"] = f"<{UNSUBSCRIBE_URL}>"
    msg.set_content(f"{text}\n\n—\nОтписаться от новостей клуба Vitalis: {UNSUBSCRIBE_URL}")
    return await _send(msg, cfg)
