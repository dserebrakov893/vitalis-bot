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
    delivery = Delivery(bot, sm)
    dp = build_dispatcher(sm, delivery)
    await setup_bot_profile(bot)
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
