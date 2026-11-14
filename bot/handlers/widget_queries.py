"""Апдейты widget_query / shared_widget_query.

aiogram их не знает: такой Update не проходит роутинг (event_type не определяется), поэтому
перехватываем его во внешней мидлвари диспетчера и разбираем из Update.model_extra.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import Update

from bot.handlers.start import begin_registration
from bot.handlers.user_commands import ask_cancel

log = logging.getLogger(__name__)
WIDGET_UPDATE_FIELDS = ("widget_query", "shared_widget_query")


class WidgetQueryMiddleware(BaseMiddleware):
    def __init__(self, dp: Dispatcher):
        self.dp = dp

    async def __call__(
        self, handler: Callable[[Update, dict[str, Any]], Awaitable[Any]], event: Update, data: dict[str, Any]
    ) -> Any:
        extra = event.model_extra or {}
        field = next((f for f in WIDGET_UPDATE_FIELDS if f in extra), None)
        if field is None:
            return await handler(event, data)

        query = extra[field]
        if not isinstance(query, dict):
            return None
        if field == "shared_widget_query":
            query = {k: v for k, v in query.items() if k not in ("chat", "message_id")}

        bot: Bot = data["bot"]
        delivery = data["delivery"]
        sm = data["sm"]
        widgets = delivery.widgets
        if widgets is None:
            log.warning("widget query received but widgets are not configured")
            return None

        async def on_register(chat_id: int, user_id: int, event_id: int) -> None:
            state = self.dp.fsm.get_context(bot=bot, chat_id=chat_id, user_id=user_id)
            await begin_registration(bot, chat_id, user_id, event_id, state, sm, delivery)

        async def on_cancel(user_id: int) -> None:
            await ask_cancel(lambda text, **kw: bot.send_message(user_id, text, **kw), sm, user_id)

        await widgets.handle_query(query, on_register=on_register, on_cancel=on_cancel)
        return None
