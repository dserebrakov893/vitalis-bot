"""/cancel, /club, /about, /unsubscribe, /delete_me."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot import texts as t
from bot.config import settings
from bot.db.models import Event, Registration
from bot.services import registration as regs
from bot.services.delivery import Delivery

log = logging.getLogger(__name__)
router = Router(name="user_commands")


async def ask_cancel(message_or_chat_send, sm: async_sessionmaker, user_id: int) -> None:
    async with sm() as s:
        active = await regs.get_active_registration(s, user_id)
        event = await s.get(Event, active.event_id) if active is not None else None
    if active is None or event.starts_at <= regs.utcnow():
        await message_or_chat_send(t.CANCEL_NOTHING)
        return
    await message_or_chat_send(t.CANCEL_CONFIRM, reply_markup=kb.cancel_confirm_kb(active.event_id))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, sm: async_sessionmaker) -> None:
    await ask_cancel(message.answer, sm, message.from_user.id)


@router.callback_query(F.data.startswith("cx:ask:"))
async def cb_cancel_ask(call: CallbackQuery, sm: async_sessionmaker) -> None:
    await call.answer()
    await ask_cancel(call.message.answer, sm, call.from_user.id)


async def do_cancel(user_id: int, event_id: int, sm: async_sessionmaker, delivery: Delivery) -> bool:
    res = await regs.cancel(sm, user_id, event_id)
    if res.cancelled is None:
        return False
    log.info("cancelled user=%s event=%s promoted=%s", user_id, event_id, [r.user_id for r in res.promoted])
    if delivery.widgets is not None:
        await delivery.widgets.ticket_cancelled(res.cancelled)
    await delivery.notify_promoted(res.promoted)
    await delivery.seats_changed(event_id)
    return True


@router.callback_query(F.data.startswith("cx:yes:"))
async def cb_cancel_yes(call: CallbackQuery, sm: async_sessionmaker, delivery: Delivery) -> None:
    await call.answer()
    try:
        event_id = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        return
    ok = await do_cancel(call.from_user.id, event_id, sm, delivery)
    try:
        await call.message.edit_text(t.CANCEL_DONE if ok else t.CANCEL_NOTHING)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data == "cx:no")
async def cb_cancel_no(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.edit_text(t.CANCEL_KEPT)
    except Exception:  # noqa: BLE001
        pass


# /club и /about временно отключены (14.11.2026): нет ссылок на канал и чат, текст «О клубе» на согласовании.
# Чтобы вернуть: раскомментировать обработчики и строки в texts.COMMANDS.
# @router.message(Command("club"))
async def cmd_club(message: Message) -> None:
    await message.answer(t.club_text(settings.club_channel_url, settings.club_chat_url), disable_web_page_preview=True)


# @router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(t.about_text(settings.privacy_policy_url), disable_web_page_preview=True)


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, sm: async_sessionmaker) -> None:
    await regs.unsubscribe(sm, message.from_user.id)
    log.info("unsubscribed user=%s", message.from_user.id)
    await message.answer(t.UNSUBSCRIBED, reply_markup=kb.main_kb())


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message) -> None:
    await message.answer(t.DELETE_CONFIRM, reply_markup=kb.delete_confirm_kb())


@router.callback_query(F.data == "del:yes")
async def cb_delete_yes(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    await call.answer()
    user_id = call.from_user.id
    # Билеты-виджеты аннулируем до удаления: после него номера сообщений уже не найти
    async with sm() as s:
        event_ids = set(
            (await s.execute(select(Registration.event_id).where(Registration.user_id == user_id))).scalars()
        )
    if delivery.widgets is not None:
        await delivery.widgets.user_tickets_cancelled(user_id)
    await state.clear()
    promoted = await regs.delete_user(sm, user_id)
    await state.clear()  # delete_user чистит FSM в БД, а этот вызов сбрасывает и кэш контекста
    log.info("deleted user=%s", user_id)
    try:
        await call.message.edit_text(t.DELETE_DONE)
    except Exception:  # noqa: BLE001
        await call.message.answer(t.DELETE_DONE)
    await delivery.notify_promoted(promoted)
    for event_id in event_ids:
        await delivery.seats_changed(event_id)


@router.callback_query(F.data == "del:no")
async def cb_delete_no(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.edit_text(t.DELETE_KEPT)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(call: CallbackQuery, sm: async_sessionmaker, delivery: Delivery) -> None:
    from bot.services.reminders import RATED_TEXT, save_rating

    try:
        _, event_id, score = call.data.split(":")
        reg = await save_rating(sm, call.from_user.id, int(event_id), int(score))
    except ValueError:
        reg = None
    await call.answer(RATED_TEXT if reg else "")
    if reg is None:
        return
    log.info("rated user=%s event=%s", call.from_user.id, reg.event_id)
    try:
        await call.message.edit_text(f"{RATED_TEXT} Твоя оценка: {reg.feedback_score} из 5.")
    except Exception:  # noqa: BLE001
        pass
    if delivery.widgets is not None and reg.ticket_widget_status == "done":
        await delivery.widgets.set_ticket_status(reg, "done")


@router.message(F.chat.type == "private")
async def fallback_message(message: Message) -> None:
    await message.answer(t.MAIN_HINT, reply_markup=kb.main_kb())
