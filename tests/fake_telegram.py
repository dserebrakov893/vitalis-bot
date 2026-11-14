"""Поддельная сессия aiogram: записывает вызовы Bot API и отвечает правдоподобными объектами."""
from __future__ import annotations

import itertools
import time
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User

BOT_ID = 777


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ids = itertools.count(1000)
        self.raw_calls: list[tuple[str, dict[str, Any]]] = []

    async def close(self) -> None:
        return None

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        name = type(method).__name__
        params = method.model_dump(exclude_none=True)
        self.calls.append((name, params))
        returning = getattr(method, "__returning__", None)
        if returning is Message:
            chat_id = params.get("chat_id", 0)
            return Message(
                message_id=next(self._ids),
                date=int(time.time()),
                chat=Chat(id=chat_id, type="private"),
                text=params.get("text"),
            )
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="Vitalis", username="vitalis_business_club_bot")
        return True

    # удобства для проверок
    def texts(self) -> list[str]:
        return [p.get("text", "") for n, p in self.calls if n in ("SendMessage", "EditMessageText")]

    def last_text(self) -> str:
        return self.texts()[-1]

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def reset(self) -> None:
        self.calls.clear()


def make_bot() -> tuple[Bot, FakeSession]:
    from aiogram.client.default import DefaultBotProperties

    session = FakeSession()
    bot = Bot("123456:" + "A" * 35, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    return bot, session


_upd = itertools.count(1)
_msg = itertools.count(1)


def _user(uid: int, name: str = "Иван") -> dict:
    return {"id": uid, "is_bot": False, "first_name": name, "username": f"user{uid}"}


def text_update(uid: int, text: str) -> Update:
    return Update.model_validate(
        {
            "update_id": next(_upd),
            "message": {
                "message_id": next(_msg),
                "date": int(time.time()),
                "chat": {"id": uid, "type": "private"},
                "from": _user(uid),
                "text": text,
            },
        }
    )


def contact_update(uid: int, contact_user_id: int | None, phone: str = "+7 999 000-00-00") -> Update:
    contact = {"phone_number": phone, "first_name": "Иван"}
    if contact_user_id is not None:
        contact["user_id"] = contact_user_id
    return Update.model_validate(
        {
            "update_id": next(_upd),
            "message": {
                "message_id": next(_msg),
                "date": int(time.time()),
                "chat": {"id": uid, "type": "private"},
                "from": _user(uid),
                "contact": contact,
            },
        }
    )


def callback_update(uid: int, data: str) -> Update:
    return Update.model_validate(
        {
            "update_id": next(_upd),
            "callback_query": {
                "id": str(next(_upd)),
                "from": _user(uid),
                "chat_instance": "1",
                "data": data,
                "message": {
                    "message_id": next(_msg),
                    "date": int(time.time()),
                    "chat": {"id": uid, "type": "private"},
                    "from": {"id": BOT_ID, "is_bot": True, "first_name": "Vitalis"},
                    "text": "…",
                },
            },
        }
    )


def raw_update(payload: dict) -> Update:
    return Update.model_validate({"update_id": next(_upd), **payload})
