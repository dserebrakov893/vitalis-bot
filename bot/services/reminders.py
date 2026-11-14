"""Напоминания и смена статусов билета по расписанию.

Фоновая задача раз в минуту смотрит в БД. Флаги reminded_*/live_sent/feedback_sent лежат в БД
и «захватываются» атомарным UPDATE ... WHERE flag = false RETURNING до отправки: даже два процесса
(например, при перекрытии деплоев на Railway) не отправят одно сообщение дважды. Если отправка
упала не из-за блокировки бота, флаг возвращается и сообщение уйдёт на следующем тике.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from bot import keyboards as kb
from bot.db.models import Event, Registration
from bot.formatting import fmt_datetime, fmt_time
from bot.services.delivery import SEND_DELAY, Delivery
from bot.services.registration import utcnow

log = logging.getLogger(__name__)

TICK_SECONDS = 60
FEEDBACK_AFTER = timedelta(hours=3)
FEEDBACK_WINDOW = timedelta(days=2)  # не просим оценку, если бот лежал дольше


@dataclass(frozen=True)
class Stage:
    flag: str
    offset: timedelta  # относительно starts_at
    until: timedelta  # после этого момента этап пропускаем


STAGES = (
    Stage("reminded_24h", -timedelta(hours=24), -timedelta(hours=2)),
    Stage("reminded_2h", -timedelta(hours=2), timedelta(0)),
    Stage("live_sent", timedelta(0), FEEDBACK_AFTER),
    Stage("feedback_sent", FEEDBACK_AFTER, FEEDBACK_AFTER + FEEDBACK_WINDOW),
)


def text_24h(event: Event) -> str:
    lines = [
        f"Напоминаем: встреча клуба Vitalis {escape(fmt_datetime(event.starts_at))} (МСК).",
        f"Спикер: {escape(event.speaker)}.",
    ]
    if event.venue_text:
        lines.append(f"Место: {escape(event.venue_text)}")
    lines.append("\nЕсли планы поменялись, нажми «Не смогу прийти»: место уйдёт тому, кто в листе ожидания.")
    return "\n".join(lines)


def text_2h(event: Event) -> str:
    venue = f"\nАдрес: {escape(event.venue_text)}" if event.venue_text else ""
    return f"Начинаем через 2 часа, в {fmt_time(event.starts_at)}.{venue}\nБилет всегда под рукой: /ticket"


FEEDBACK_TEXT = "Спасибо, что был на встрече Vitalis! Оцени её от 1 до 5, это поможет сделать следующие лучше."
RATED_TEXT = "Спасибо за оценку!"


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
                            Event.starts_at + stage.offset <= now,
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
                late = now >= event.starts_at + stage.until
                # Записался уже после момента напоминания: билет он только что получил, текст не шлём
                fresh = stage.flag in ("reminded_24h", "reminded_2h") and reg.created_at > event.starts_at + stage.offset
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
            await self.bot.send_message(uid, FEEDBACK_TEXT, reply_markup=kb.rating_kb(event.id))

    async def run_forever(self) -> None:
        log.info("reminders loop started")
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("reminders tick failed")
            await asyncio.sleep(TICK_SECONDS)


async def save_rating(sm: async_sessionmaker, user_id: int, event_id: int, score: int) -> Registration | None:
    if not 1 <= score <= 5:
        return None
    async with sm() as s, s.begin():
        res = await s.execute(
            update(Registration)
            .where(and_(Registration.user_id == user_id, Registration.event_id == event_id))
            .values(feedback_score=score)
            .returning(Registration)
        )
        return res.scalar_one_or_none()
