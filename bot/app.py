"""Сборка диспетчера (используется и в боевом запуске, и в тестах)."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import texts as t
from bot.db.fsm_storage import PgStorage
from bot.handlers import admin, checkin, feedback, form, start, support, user_commands
from bot.handlers.widget_queries import WidgetQueryMiddleware
from bot.services.delivery import Delivery

log = logging.getLogger(__name__)

# aiogram не знает апдейты виджетов, поэтому список передаём явно
ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member", "widget_query", "shared_widget_query"]


def build_dispatcher(sm: async_sessionmaker, delivery: Delivery, extra_routers=()) -> Dispatcher:
    dp = Dispatcher(storage=PgStorage(sm))
    dp["sm"] = sm
    dp["delivery"] = delivery
    dp.update.outer_middleware(WidgetQueryMiddleware(dp))
    for r in extra_routers:
        dp.include_router(r)
    dp.include_router(support.router)  # первым: ответы поддержки не должны попадать в другие обработчики
    dp.include_router(start.router)
    dp.include_router(form.router)
    dp.include_router(checkin.router)
    dp.include_router(admin.list_router)
    dp.include_router(admin.router)
    dp.include_router(feedback.router)
    dp.include_router(user_commands.router)  # последним: в нём «ловушка» для прочих сообщений

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        upd = event.update
        uid = None
        for part in (upd.message, upd.callback_query):
            if part is not None and part.from_user is not None:
                uid = part.from_user.id
        # В лог только тип ошибки и Telegram ID, без текста сообщений (там могут быть ПДн)
        log.error("handler error user=%s update=%s: %r", uid, upd.update_id, event.exception, exc_info=event.exception)
        return True

    return dp


async def setup_bot_profile(bot: Bot) -> None:
    """Имя, описание и меню команд. Ошибки не критичны (например, лимит на смену имени)."""
    calls = [
        ("commands", bot.set_my_commands([BotCommand(command=c, description=d) for c, d in t.COMMANDS])),
        ("name", bot.set_my_name(name=t.BOT_NAME)),
        ("description", bot.set_my_description(description=t.BOT_DESCRIPTION)),
        ("short description", bot.set_my_short_description(short_description=t.BOT_ABOUT)),
    ]
    for what, coro in calls:
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            log.warning("cannot set bot %s: %r", what, e)
