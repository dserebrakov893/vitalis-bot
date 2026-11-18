"""Запуск: python -m bot (long polling)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.app import ALLOWED_UPDATES, build_dispatcher, setup_bot_profile
from bot.config import settings
from bot.db.engine import make_engine, make_sessionmaker
from bot.services.delivery import Delivery
from bot.services.pdf_ticket import render_ticket_pdf
from bot.services.reminders import Reminders
from bot.widgets.service import WidgetService


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # aiogram пишет в INFO содержимое апдейтов только на DEBUG, но на всякий случай приглушаем
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main() -> None:
    _setup_logging()
    settings.validate_for_run()
    engine = make_engine(settings.database_url, settings.pg_ssl_root_cert)
    sm = make_sessionmaker(engine)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    widgets = WidgetService(bot, sm, enabled=settings.widgets_enabled)
    delivery = Delivery(bot, sm, widgets=widgets, pdf_renderer=render_ticket_pdf)
    logging.getLogger(__name__).info("widgets enabled: %s", settings.widgets_enabled)
    dp = build_dispatcher(sm, delivery)
    await setup_bot_profile(bot)
    reminders_task = asyncio.create_task(Reminders(sm, delivery).run_forever())
    web_runner = None
    if settings.port:
        from bot.handlers.admin import ADMIN_IDS
        from bot.webapp.server import build_webapp, start_webapp

        web_runner = await start_webapp(build_webapp(sm, settings.bot_token, lambda: ADMIN_IDS), settings.port)
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        reminders_task.cancel()
        if web_runner is not None:
            await web_runner.cleanup()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
