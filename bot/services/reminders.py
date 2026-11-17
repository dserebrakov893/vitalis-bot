"""Напоминания и смена статусов билета по расписанию.

Фоновая задача раз в минуту смотрит в БД. Флаги reminded_*/live_sent/feedback_sent лежат в БД
и «захватываются» атомарным UPDATE ... WHERE flag = false RETURNING до отправки: даже два процесса
(например, при перекрытии старого и нового контейнера во время деплоя) не отправят одно сообщение дважды. Если отправка
упала не из-за блокировки бота, флаг возвращается и сообщение уйдёт на следующем тике.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable
from datetime import datetime, timedelta
from html import escape

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from bot import keyboards as kb
from bot import texts as t
from bot.db.models import Event, Registration
from bot.formatting import EVENT_DURATION, event_end, fmt_time
from bot.services.delivery import SEND_DELAY, Delivery, event_details
from bot.services.registration import utcnow

log = logging.getLogger(__name__)

TICK_SECONDS = 60
FEEDBACK_AFTER_END = timedelta(minutes=30)  # опрос через 30 минут после окончания встречи
FEEDBACK_WINDOW = timedelta(days=2)  # не просим оценку, если бот лежал дольше
# Окончание встречи в SQL: ends_at, а если не задано, начало + 3 часа
EVENT_END_SQL = func.coalesce(Event.ends_at, Event.starts_at + EVENT_DURATION)


@dataclass(frozen=True)
class Stage:
    flag: str
    due: Callable  # SQL-выражение момента отправки
    late: Callable[[Event], datetime]  # после этого момента этап пропускаем
    base: Callable[[Event], datetime]  # момент этапа (для проверки «записался позже»)


def _feedback_at(e: Event) -> datetime:
    return event_end(e) + FEEDBACK_AFTER_END


STAGES = (
    Stage("reminded_24h", lambda: Event.starts_at - timedelta(hours=24),
          lambda e: e.starts_at - timedelta(hours=2), lambda e: e.starts_at - timedelta(hours=24)),
    Stage("reminded_2h", lambda: Event.starts_at - timedelta(hours=2),
          lambda e: e.starts_at, lambda e: e.starts_at - timedelta(hours=2)),
    Stage("live_sent", lambda: Event.starts_at, _feedback_at, lambda e: e.starts_at),
    Stage("feedback_sent", lambda: EVENT_END_SQL + FEEDBACK_AFTER_END,
          lambda e: _feedback_at(e) + FEEDBACK_WINDOW, _feedback_at),
)


def text_24h(event: Event) -> str:
    return "\n".join(
        [
            "<b>Завтра встреча клуба Vitalis</b>",
            "",
            escape(event.title),
            *event_details(event),
            "",
            "Если планы поменялись, нажми «Не смогу прийти»: место уйдёт тому, кто в листе ожидания.",
        ]
    )


def text_2h(event: Event) -> str:
    lines = [f"<b>Начинаем через 2 часа, в {fmt_time(event.starts_at)}</b>"]
    if event.venue_text:
        lines += ["", f"<b>Где:</b> {escape(event.venue_text)}"]
    lines += ["", "Билет всегда под рукой: кнопка «Мой билет» внизу."]
    return "\n".join(lines)




class Reminders:
    def __init__(self, sm: async_sessionmaker, delivery: Delivery):
        self.sm = sm
        self.delivery = delivery
        self.bot = delivery.bot

    async def _claim(self, reg_id: int, flag: str, value: bool = True) -> bool:
        col = getattr(Registration, flag)
        async with self.sm() as s, s.begin():
            res = await s.execute(
                update(Registration)
                .where(Registration.id == reg_id, col.is_(not value), Registration.state == "registered")
                .values({flag: value})
                .returning(Registration.id)
            )
            return res.first() is not None

    async def _due(self, stage: Stage, now: datetime) -> list[Registration]:
        col = getattr(Registration, stage.flag)
        async with self.sm() as s:
            return list(
                (
                    await s.execute(
                        select(Registration)
                        .join(Event)
                        .options(selectinload(Registration.event), selectinload(Registration.user))
                        .where(
                            Registration.state == "registered",
                            col.is_(False),
                            stage.due() <= now,
                        )
                        .order_by(Registration.id)
                    )
                ).scalars()
            )

    async def tick(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        sent: dict[str, int] = {}
        for stage in STAGES:
            for reg in await self._due(stage, now):
                event = reg.event
                if not await self._claim(reg.id, stage.flag):
                    continue  # уже обработано другим процессом
                late = now >= stage.late(event)
                # Записался уже после момента напоминания: билет он только что получил, текст не шлём
                fresh = stage.flag in ("reminded_24h", "reminded_2h") and reg.created_at > stage.base(event)
                if late or (fresh and stage.flag == "reminded_24h"):
                    continue  # этап пропущен (бот лежал или человек записался позже), флаг остаётся
                try:
                    await self._run_stage(stage.flag, reg, event, silent=fresh)
                except TelegramForbiddenError:
                    log.info("skip %s: user %s blocked the bot", stage.flag, reg.user_id)
                except Exception:  # noqa: BLE001
                    log.exception("stage %s failed for reg %s, will retry", stage.flag, reg.id)
                    await self._claim(reg.id, stage.flag, value=False)
                    continue
                sent[stage.flag] = sent.get(stage.flag, 0) + 1
                await asyncio.sleep(SEND_DELAY)
        if sent:
            log.info("reminders tick: %s", sent)
        return sent

    async def _run_stage(self, flag: str, reg: Registration, event: Event, silent: bool = False) -> None:
        widgets = self.delivery.widgets
        uid = reg.user_id
        if flag == "reminded_24h":
            await self.bot.send_message(
                uid, text_24h(event),
                reply_markup=kb.reminder_kb(event.id, event.venue_url), disable_web_page_preview=True,
            )
        elif flag == "reminded_2h":
            if not silent:
                await self.bot.send_message(uid, text_2h(event), disable_web_page_preview=True)
            if widgets is not None:
                await widgets.set_ticket_status(reg, "soon", reg.user)
        elif flag == "live_sent":
            # по брифу в момент начала только меняем состояние билета, без сообщения
            if widgets is not None:
                await widgets.set_ticket_status(reg, "live", reg.user)
        elif flag == "feedback_sent":
            if widgets is not None:
                await widgets.set_ticket_status(reg, "done", reg.user)
            # тот же опрос открывается и по кнопке «Отзыв» (bot/handlers/feedback.py)
            await self.bot.send_message(uid, t.fb_rate_text(event.title), reply_markup=kb.rating_kb(event.id))

    async def run_forever(self) -> None:
        log.info("reminders loop started")
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("reminders tick failed")
            await asyncio.sleep(TICK_SECONDS)
