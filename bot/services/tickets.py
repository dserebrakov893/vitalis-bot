"""Подпись билета для QR: VTL1.<event_id>.<ticket_no>.<sig>. Персональных данных в QR нет."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Registration

PREFIX = "VTL1"
_PAYLOAD = re.compile(r"^VTL1\.(\d{1,9})\.(\d{1,9})\.([A-Z2-7]{10})$")


def _secret(secret: str | None) -> bytes:
    value = secret if secret is not None else settings.ticket_hmac_secret
    if not value:
        raise RuntimeError("TICKET_HMAC_SECRET не задан")
    return value.encode()


def sign(event_id: int, ticket_no: int, secret: str | None = None) -> str:
    mac = hmac.new(_secret(secret), f"{event_id}.{ticket_no}".encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode()[:10]


def make_payload(event_id: int, ticket_no: int, secret: str | None = None) -> str:
    return f"{PREFIX}.{event_id}.{ticket_no}.{sign(event_id, ticket_no, secret)}"


def parse_payload(payload: str, secret: str | None = None) -> tuple[int, int] | None:
    """Проверяет формат и подпись. Возвращает (event_id, ticket_no) или None."""
    m = _PAYLOAD.fullmatch((payload or "").strip())
    if not m:
        return None
    event_id, ticket_no, sig = int(m.group(1)), int(m.group(2)), m.group(3)
    if not hmac.compare_digest(sig, sign(event_id, ticket_no, secret)):
        return None
    return event_id, ticket_no


async def verify_ticket(session: AsyncSession, payload: str, secret: str | None = None) -> Registration | None:
    """Задел под чекин: действующая регистрация по QR или None (подделка, отмена, удаление)."""
    parsed = parse_payload(payload, secret)
    if parsed is None:
        return None
    event_id, ticket_no = parsed
    return (
        await session.execute(
            select(Registration).where(
                Registration.event_id == event_id,
                Registration.ticket_no == ticket_no,
                Registration.state == "registered",
            )
        )
    ).scalar_one_or_none()
