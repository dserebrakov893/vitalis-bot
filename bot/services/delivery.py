"""Отправка карточки встречи, билета и уведомлений. Здесь же выбор: виджет или fallback."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot import keyboards as kb
from bot import texts as t
from bot.db.models import Event, Registration, User
from bot.formatting import calendar_url, first_name, fmt_datetime, fmt_weekday
from bot.services import mailer
from bot.services import registration as regs

log = logging.getLogger(__name__)

SEND_DELAY = 0.035  # лимит Telegram ~30 сообщений в секунду


def event_heading(event: Event) -> str:
    """Короткая подпись для подписей виджетов и календаря."""
    title = event.title[:1].lower() + event.title[1:] if event.title else ""
    return f"Vitalis · {title}"


def event_details(event: Event) -> list[str]:
    """Когда / Где / Спикер: одинаково в карточке, билете и напоминаниях."""
    when = f"{fmt_weekday(event.starts_at)}, {fmt_datetime(event.starts_at)} (МСК)"
    lines = [f"<b>Когда:</b> {escape(when)}"]
    if event.venue_text:
        lines.append(f"<b>Где:</b> {escape(event.venue_text)}")
    lines.append(f"<b>Спикер:</b> {escape(event.speaker)}")
    return lines


def event_card_text(event: Event, left: int) -> str:
    seats = f"Осталось мест: {left}" if left > 0 else "Мест нет, но можно встать в лист ожидания"
    return "\n".join(
        [f"<b>{escape(event.title)}</b>", "<i>Бизнес-клуб Vitalis</i>", "", *event_details(event), "", seats]
    )


TICKET_STATUS = {
    "registered": "Ты в списке. На входе покажи QR-код из PDF-билета.",
    "waitlist": "Ты в листе ожидания.",
    "cancelled": "Билет аннулирован.",
}


def ticket_text(user: User, event: Event, reg: Registration) -> str:
    return "\n".join(
        [
            f"<b>Твой билет №{reg.ticket_no}</b>",
            escape(user.full_name or user.tg_first_name or ""),
            "",
            escape(event.title),
            *event_details(event),
            "",
            TICKET_STATUS[reg.state],
        ]
    )


async def safe_call(coro_factory, *, what: str, user_id: int):
    """Вызов Bot API: 403 (бот заблокирован) логируем и пропускаем, флуд-лимит пережидаем."""
    for _ in range(3):
        try:
            return await coro_factory()
        except TelegramForbiddenError:
            log.info("skip %s: user %s blocked the bot", what, user_id)
            return None
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
    log.warning("give up %s for user %s after retries", what, user_id)
    return None


async def load_registration(session: AsyncSession, reg_id: int) -> Registration | None:
    return await session.get(
        Registration, reg_id, options=[selectinload(Registration.user), selectinload(Registration.event)]
    )


class Delivery:
    """Собирает всё, что бот отправляет пользователю. Виджеты и PDF подключаются здесь."""

    def __init__(self, bot: Bot, sm: async_sessionmaker, widgets=None, pdf_renderer=None):
        self.bot = bot
        self.sm = sm
        self.widgets = widgets  # bot.widgets.service.WidgetService | None
        self.pdf_renderer = pdf_renderer  # callable(user, event, reg) -> bytes | None

    def widgets_on(self, prefer_widgets: bool) -> bool:
        return prefer_widgets and self.widgets is not None and self.widgets.enabled

    # ---------- карточка ----------
    async def send_event_card(self, chat_id: int, event: Event, *, prefer_widgets: bool = True) -> None:
        async with self.sm() as s:
            left = await regs.seats_left(s, event)
        if self.widgets_on(prefer_widgets):
            if await self.widgets.send_event_card(chat_id, event, left):
                return
        await safe_call(
            lambda: self.bot.send_message(chat_id, event_card_text(event, left), reply_markup=kb.card_kb(event.id)),
            what="card",
            user_id=chat_id,
        )

    # ---------- билет ----------
    async def send_ticket(self, reg_id: int, *, prefer_widgets: bool = True, intro: str | None = None,
                          send_email: bool = False) -> None:
        async with self.sm() as s:
            reg = await load_registration(s, reg_id)
        if reg is None or reg.state != "registered":
            return
        user, event = reg.user, reg.event
        chat_id = user.id
        if intro:
            await safe_call(lambda: self.bot.send_message(chat_id, intro, reply_markup=kb.main_kb()),
                            what="ticket intro", user_id=chat_id)

        sent_widget = False
        if self.widgets_on(prefer_widgets):
            sent_widget = await self.widgets.send_ticket(user, event, reg)
        if not sent_widget:
            await safe_call(
                lambda: self.bot.send_message(
                    chat_id,
                    ticket_text(user, event, reg),
                    reply_markup=kb.ticket_kb(
                        event.id, calendar_url(event.title, event.starts_at, event.venue_text), event.venue_url
                    ),
                    disable_web_page_preview=True,
                ),
                what="ticket text",
                user_id=chat_id,
            )
        await self.send_ticket_pdf(user, event, reg, send_email=send_email)

    async def send_ticket_pdf(self, user: User, event: Event, reg: Registration, send_email: bool = False) -> None:
        if self.pdf_renderer is None:
            return
        from aiogram.types import BufferedInputFile

        try:
            data = await asyncio.to_thread(self.pdf_renderer, user, event, reg)
        except Exception:  # noqa: BLE001 — PDF это план Б, его поломка не должна ронять регистрацию
            log.exception("pdf render failed for reg %s", reg.id)
            return
        file = BufferedInputFile(data, filename=f"Vitalis_bilet_{reg.ticket_no}.pdf")
        # PDF приходит с каждым билетом, поэтому он же ставит клавиатуру внизу
        await safe_call(lambda: self.bot.send_document(user.id, file, reply_markup=kb.main_kb()),
                        what="ticket pdf", user_id=user.id)
        if send_email and user.email and mailer.enabled():
            await mailer.send_ticket(user.email, user.full_name or "", data, reg.ticket_no)

    # ---------- после регистрации / отмены ----------
    async def after_registration(self, result: regs.RegResult, *, prefer_widgets: bool = True) -> None:
        reg = result.registration
        async with self.sm() as s:
            user = await s.get(User, reg.user_id)
        if reg.state == "registered":
            intro = t.registered_text(first_name(user.full_name, user.tg_first_name), reg.ticket_no, self.pdf_renderer is not None)
            await self.send_ticket(reg.id, prefer_widgets=prefer_widgets, intro=intro if result.created else None,
                                   send_email=result.created)
        else:
            text = t.waitlist_text(result.waitlist_position or 1)
            await safe_call(lambda: self.bot.send_message(user.id, text, reply_markup=kb.main_kb()),
                            what="waitlist", user_id=user.id)
        await self.seats_changed(reg.event_id)

    async def notify_promoted(self, promoted: list[Registration]) -> None:
        for reg in promoted:
            await self.send_ticket(reg.id, intro=t.PROMOTED, send_email=True)
            await asyncio.sleep(SEND_DELAY)

    async def seats_changed(self, event_id: int) -> None:
        """Обновить счётчик мест в shared-виджете (с пачкованием, не чаще раза в секунду)."""
        if self.widgets is not None and self.widgets.enabled:
            self.widgets.schedule_card_update(event_id)
