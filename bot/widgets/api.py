"""Методы Bot API 10.4 для виджетов, которых нет в aiogram.

Оформлены как TelegramMethod, поэтому идут через обычную сессию бота (`await bot(SendWidget(...))`):
те же таймауты, обработка ошибок и флуд-лимитов. Имена параметров сверены с core.telegram.org/bots/api.
"""
from __future__ import annotations

from typing import Any

from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message


class SendWidget(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: int | str
    html: str
    width: int | None = None
    height: int | None = None
    public_data: str | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    show_caption_above_media: bool | None = None
    allow_queries: bool | None = None
    is_shared: bool | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class SendSharedWidget(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendSharedWidget"

    chat_id: int | str
    widget_id: str
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    show_caption_above_media: bool | None = None
    disable_notification: bool | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "editMessageWidgetData"

    chat_id: int | str | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    public_data: str | None = None
    chat_data: str | None = None


class EditSharedWidgetData(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "editSharedWidgetData"

    widget_id: str
    public_data: str
    cache_time: int | None = None


class AnswerWidgetQuery(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None


def widget_of(message: Message) -> dict[str, Any] | None:
    """Поле Message.widget (aiogram его не знает, оно лежит в model_extra)."""
    extra = message.model_extra or {}
    widget = extra.get("widget")
    return widget if isinstance(widget, dict) else None
