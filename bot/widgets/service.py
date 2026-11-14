"""Отправка виджетов, смена их состояния и обработка запросов из виджетов.

Любая ошибка виджета не ломает сценарий: метод возвращает False, и Delivery шлёт fallback.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Event, Registration, User
from bot.services import registration as regs
from bot.widgets import render
from bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    EditSharedWidgetData,
    SendSharedWidget,
    SendWidget,
    widget_of,
)

log = logging.getLogger(__name__)

CARD_CACHE_TIME = 10  # как часто открытая карточка опрашивает счётчик мест, секунд
FLUSH_INTERVAL = 1.1  # shared-виджет можно править не чаще раза в секунду


def _not_modified(e: Exception) -> bool:
    return isinstance(e, TelegramBadRequest) and "not modified" in str(e).lower()


class WidgetService:
    def __init__(self, bot: Bot, sm: async_sessionmaker, enabled: bool = True, autoflush: bool = True):
        self.bot = bot
        self.sm = sm
        self.enabled = enabled
        self.autoflush = autoflush  # в тестах выключаем фоновую задачу и зовём flush_cards() руками
        self._card_lock = asyncio.Lock()
        self._dirty: set[int] = set()
        self._flusher: asyncio.Task | None = None

    # ---------- карточка встречи (shared-виджет) ----------

    async def send_event_card(self, chat_id: int, event: Event, left: int) -> bool:
        caption = render.card_caption(event, left)
        try:
            if event.shared_widget_id:
                await self.bot(SendSharedWidget(chat_id=chat_id, widget_id=event.shared_widget_id, caption=caption))
                return True
            async with self._card_lock:
                async with self.sm() as s:
                    fresh = await s.get(Event, event.id)
                if fresh.shared_widget_id:
                    await self.bot(SendSharedWidget(chat_id=chat_id, widget_id=fresh.shared_widget_id, caption=caption))
                    return True
                # Отдельного метода создания нет: shared-виджет создаёт первая отправка с is_shared
                msg = await self.bot(
                    SendWidget(
                        chat_id=chat_id,
                        html=render.event_card_html(event),
                        public_data=render.card_public_data(left, event.is_open),
                        caption=caption,
                        allow_queries=True,
                        is_shared=True,
                    )
                )
                widget_id = (widget_of(msg) or {}).get("id")
                if widget_id:
                    async with self.sm() as s, s.begin():
                        await s.execute(
                            update(Event)
                            .where(Event.id == event.id, Event.shared_widget_id.is_(None))
                            .values(shared_widget_id=str(widget_id))
                        )
                    log.info("created shared card widget %s for event %s", widget_id, event.id)
                    await self._edit_card(str(widget_id), left, event.is_open, cache_time=CARD_CACHE_TIME)
                else:
                    log.warning("sendWidget returned no widget id for event %s", event.id)
            return True
        except TelegramForbiddenError:
            return True  # бот заблокирован: fallback тоже не дойдёт
        except Exception as e:  # noqa: BLE001
            log.warning("event card widget failed for user %s, sending fallback: %r", chat_id, e)
            return False

    async def _edit_card(self, widget_id: str, left: int, is_open: bool, cache_time: int | None = None) -> None:
        try:
            await self.bot(
                EditSharedWidgetData(
                    widget_id=widget_id, public_data=render.card_public_data(left, is_open), cache_time=cache_time
                )
            )
        except Exception as e:  # noqa: BLE001
            if not _not_modified(e):
                log.warning("editSharedWidgetData failed for %s: %r", widget_id, e)

    def schedule_card_update(self, event_id: int) -> None:
        """Копим изменения и отправляем пачкой не чаще раза в секунду (лимит Telegram)."""
        self._dirty.add(event_id)
        if self.autoflush and self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while self._dirty:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self.flush_cards()

    async def flush_cards(self) -> None:
        batch, self._dirty = self._dirty, set()
        for event_id in batch:
            async with self.sm() as s:
                event = await s.get(Event, event_id)
                if event is None or not event.shared_widget_id:
                    continue
                left = await regs.seats_left(s, event)
            await self._edit_card(event.shared_widget_id, left, event.is_open and event.starts_at > regs.utcnow())
            if len(batch) > 1:
                await asyncio.sleep(FLUSH_INTERVAL)

    # ---------- именной билет (обычный виджет) ----------

    async def send_ticket(self, user: User, event: Event, reg: Registration) -> bool:
        status = "registered"
        try:
            msg = await self.bot(
                SendWidget(
                    chat_id=user.id,
                    html=render.ticket_html(event),
                    chat_data=render.ticket_chat_data(user, reg, status),
                    caption=render.ticket_caption(event, reg),
                    allow_queries=True,
                )
            )
        except TelegramForbiddenError:
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("ticket widget failed for user %s, sending fallback: %r", user.id, e)
            return False
        async with self.sm() as s, s.begin():
            await s.execute(
                update(Registration)
                .where(Registration.id == reg.id)
                .values(ticket_widget_msg_id=msg.message_id, ticket_widget_status=status)
            )
        return True

    async def set_ticket_status(self, reg: Registration, status: str, user: User | None = None) -> bool:
        """Сменить статус билета-виджета. True: записано (или виджета нет и менять нечего)."""
        if not reg.ticket_widget_msg_id:
            return True
        if user is None:
            async with self.sm() as s:
                user = await s.get(User, reg.user_id)
        try:
            await self.bot(
                EditMessageWidgetData(
                    chat_id=reg.user_id,
                    message_id=reg.ticket_widget_msg_id,
                    chat_data=render.ticket_chat_data(user, reg, status),
                )
            )
        except TelegramForbiddenError:
            pass
        except Exception as e:  # noqa: BLE001
            if not _not_modified(e):
                log.warning("editMessageWidgetData failed for reg %s: %r", reg.id, e)
                return False
        async with self.sm() as s, s.begin():
            await s.execute(update(Registration).where(Registration.id == reg.id).values(ticket_widget_status=status))
        return True

    async def ticket_cancelled(self, reg: Registration) -> None:
        await self.set_ticket_status(reg, "cancelled")

    async def user_tickets_cancelled(self, user_id: int) -> None:
        async with self.sm() as s:
            user = await s.get(User, user_id)
            rows = (
                await s.execute(
                    select(Registration).where(
                        Registration.user_id == user_id, Registration.ticket_widget_msg_id.is_not(None)
                    )
                )
            ).scalars().all()
        for reg in rows:
            await self.set_ticket_status(reg, "cancelled", user)

    # ---------- запросы из виджетов ----------

    async def answer(self, query_id: str, data: str | None = None) -> None:
        try:
            await self.bot(AnswerWidgetQuery(widget_query_id=query_id, data=data))
        except Exception as e:  # noqa: BLE001
            log.warning("answerWidgetQuery failed: %r", e)

    async def handle_query(
        self,
        query: dict,
        *,
        on_register: Callable[[int, int, int], Awaitable[None]],
        on_cancel: Callable[[int], Awaitable[None]],
    ) -> None:
        """widget_query / shared_widget_query. Отвечаем всегда, медленную работу делаем после ответа."""
        qid = str(query.get("id", ""))
        user_id = (query.get("from") or {}).get("id")
        data = str(query.get("data") or "")
        qtype = (query.get("type") or {}).get("type", "data")
        chat = query.get("chat") or {}
        if not qid or not user_id:
            return
        if qtype != "data" or chat.get("type") not in (None, "private") or "chat" not in query:
            # кошелёк, группы и пересланные копии не поддерживаем
            await self.answer(qid, '{"e":"unsupported"}')
            return
        log.info("widget query user=%s data=%s", user_id, data[:16])

        if data == "reg":
            event_id = await self._event_by_widget(query.get("widget_id"))
            await self.answer(qid, '{"ok":1}' if event_id else '{"e":"closed"}')
            if event_id:
                await on_register(chat["id"], user_id, event_id)
            return

        if data == "cancel":
            await self.answer(qid, '{"ok":1}')
            await on_cancel(user_id)
            return

        if data.startswith("rate:") and data[5:].isdigit() and 1 <= int(data[5:]) <= 5:
            reg = await self._reg_by_message(user_id, query.get("message_id"))
            if reg is None:
                await self.answer(qid, '{"e":"no_ticket"}')
                return
            score = int(data[5:])
            async with self.sm() as s, s.begin():
                await s.execute(update(Registration).where(Registration.id == reg.id).values(feedback_score=score))
            await self.answer(qid, '{"ok":1}')
            reg.feedback_score = score
            await self.set_ticket_status(reg, reg.ticket_widget_status or "done")
            return

        await self.answer(qid, '{"e":"unknown"}')

    async def _event_by_widget(self, widget_id) -> int | None:
        async with self.sm() as s:
            if widget_id:
                ev = (
                    await s.execute(select(Event).where(Event.shared_widget_id == str(widget_id)))
                ).scalar_one_or_none()
                if ev is not None:
                    return ev.id
            ev = await regs.get_current_event(s)
            return ev.id if ev else None

    async def _reg_by_message(self, user_id: int, message_id) -> Registration | None:
        async with self.sm() as s:
            if message_id:
                reg = (
                    await s.execute(
                        select(Registration).where(
                            Registration.user_id == user_id, Registration.ticket_widget_msg_id == int(message_id)
                        )
                    )
                ).scalar_one_or_none()
                if reg is not None:
                    return reg
            return await regs.get_active_registration(s, user_id)
