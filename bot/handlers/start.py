"""/start, /reg, /ticket и кнопка «Иду»."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot import texts as t
from bot.db.models import Consent, Event, User
from bot.handlers.form import Form, ask
from bot.services import registration as regs
from bot.services.delivery import Delivery, safe_call
from bot.services.validation import parse_source

log = logging.getLogger(__name__)
router = Router(name="start")


async def _prefer_widgets(state: FSMContext) -> bool:
    return not (await state.get_data()).get("fallback", False)


async def _install_keyboard(message: Message) -> None:
    """Первый вход (или после /delete_me): ставим клавиатуру внизу без лишнего сообщения в чате.

    У карточки уже есть inline-кнопка «Иду», а у сообщения может быть только одна клавиатура. Поэтому
    клавиатуру ставит служебное сообщение, которое сразу удаляется: клавиатура остаётся у клиента."""
    try:
        msg = await message.answer("⌨️", reply_markup=kb.main_kb(), disable_notification=True)
        await message.bot.delete_message(message.chat.id, msg.message_id)
    except Exception as e:  # noqa: BLE001 — клавиатура не критична
        log.warning("cannot install bottom keyboard for user %s: %r", message.from_user.id, e)


async def open_bot(message: Message, state: FSMContext, sm: async_sessionmaker, delivery: Delivery,
                   source: str | None, prefer_widgets: bool) -> None:
    user = message.from_user
    old = await state.get_data()
    src = source or old.get("source")
    await state.clear()
    await state.update_data(source=src, fallback=not prefer_widgets, kb=kb.MAIN_KB_VERSION)
    if old.get("kb") != kb.MAIN_KB_VERSION:
        await _install_keyboard(message)
    async with sm() as s, s.begin():
        await regs.touch_user(s, user.id, user.username, user.first_name, source)
    async with sm() as s:
        active = await regs.get_active_registration(s, user.id)
        event = await regs.get_current_event(s)
    log.info("start user=%s source=%s widgets=%s", user.id, src, prefer_widgets)

    if active is not None and active.state == "registered":
        await delivery.send_ticket(active.id, prefer_widgets=prefer_widgets)
        return
    if active is not None and active.state == "waitlist":
        async with sm() as s:
            pos = await regs.waitlist_position(s, active)
        await message.answer(t.ALREADY_WAITLIST.format(position=pos), reply_markup=kb.main_kb())
        return
    if event is None:
        await message.answer(t.NO_EVENTS, reply_markup=kb.main_kb())
        return
    await delivery.send_event_card(message.chat.id, event, prefer_widgets=prefer_widgets)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, sm: async_sessionmaker,
                    delivery: Delivery) -> None:
    if (command.args or "").strip() == "unsubscribe":
        # ссылка на отписку из писем рассылки
        await regs.unsubscribe(sm, message.from_user.id)
        await message.answer(t.UNSUBSCRIBED)
        return
    await open_bot(message, state, sm, delivery, parse_source(command.args), prefer_widgets=True)


@router.message(Command("reg"))
async def cmd_reg(message: Message, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    await open_bot(message, state, sm, delivery, None, prefer_widgets=False)


@router.message(Command("ticket"))
async def cmd_ticket(message: Message, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    async with sm() as s:
        active = await regs.get_active_registration(s, message.from_user.id)
        pos = await regs.waitlist_position(s, active) if active is not None else None
    if active is None:
        await message.answer(t.NO_TICKET, reply_markup=kb.main_kb())
    elif active.state == "waitlist":
        await message.answer(t.ALREADY_WAITLIST.format(position=pos), reply_markup=kb.main_kb())
    else:
        await delivery.send_ticket(active.id, prefer_widgets=await _prefer_widgets(state))


async def _has_profile(sm: async_sessionmaker, user_id: int) -> bool:
    async with sm() as s:
        user = await s.get(User, user_id)
        if user is None or not user.full_name or not user.phone or not user.email:
            return False
        consent = (
            await s.execute(select(Consent.id).where(Consent.user_id == user_id, Consent.type == "pdn").limit(1))
        ).first()
        return consent is not None


async def begin_registration(bot: Bot, chat_id: int, user_id: int, event_id: int, state: FSMContext,
                             sm: async_sessionmaker, delivery: Delivery) -> None:
    """Общая точка входа: кнопка «Иду» в fallback-карточке и запрос "reg" из виджета."""
    data = await state.get_data()
    prefer_widgets = not data.get("fallback", False)
    async with sm() as s:
        event = await s.get(Event, event_id)
        existing = await regs.get_registration(s, user_id, event_id)
        pos = await regs.waitlist_position(s, existing) if existing is not None else None
    if event is None or not event.is_open or event.starts_at <= regs.utcnow():
        await safe_call(lambda: bot.send_message(chat_id, t.REG_CLOSED), what="closed", user_id=user_id)
        return
    if existing is not None and existing.state == "registered":
        await delivery.send_ticket(existing.id, prefer_widgets=prefer_widgets)
        return
    if existing is not None and existing.state == "waitlist":
        await bot.send_message(chat_id, t.ALREADY_WAITLIST.format(position=pos))
        return
    if await state.get_state() is not None:
        return  # анкета уже идёт, повторный тап по «Иду» игнорируем

    if await _has_profile(sm, user_id):
        # анкета и согласие уже есть (повторная встреча или регистрация после отмены)
        try:
            result = await regs.register(sm, user_id, event_id, data.get("source"))
        except regs.RegistrationClosed:
            await bot.send_message(chat_id, t.REG_CLOSED)
            return
        if result.created:
            await delivery.after_registration(result, prefer_widgets=prefer_widgets)
        return

    await state.update_data(event_id=event_id, interests=[])
    await ask(bot, chat_id, state, Form.full_name)


@router.callback_query(F.data.startswith("go:"))
async def cb_go(call: CallbackQuery, bot: Bot, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    await call.answer()
    try:
        event_id = int(call.data.split(":", 1)[1])
    except ValueError:
        return
    await begin_registration(bot, call.message.chat.id, call.from_user.id, event_id, state, sm, delivery)


# ---------- постоянная клавиатура внизу ----------
# Обработчики в этом роутере, а он подключён раньше анкеты: кнопка работает на любом шаге.

@router.message(F.text.in_(t.MENU_EVENT_ALIASES))
async def menu_event(message: Message, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    await open_bot(message, state, sm, delivery, None, prefer_widgets=await _prefer_widgets(state))


@router.message(F.text.in_(t.MENU_TICKET_ALIASES))
async def menu_ticket(message: Message, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    await cmd_ticket(message, state, sm, delivery)


@router.message(F.text.in_(t.MENU_CANCEL_ALIASES))
async def menu_cancel(message: Message, sm: async_sessionmaker) -> None:
    from bot.handlers.user_commands import ask_cancel

    await ask_cancel(message.answer, sm, message.from_user.id)
