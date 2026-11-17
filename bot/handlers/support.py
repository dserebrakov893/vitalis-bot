"""«🆘 Поддержка»: вопрос пользователя уходит на рабочий аккаунт поддержки, ответ возвращается через бота.

Отвечать нужно свайпом «Ответить» на сообщение бота с вопросом: в нём есть метка #id<номер>, по которой
бот понимает, кому переслать ответ. Личный аккаунт поддержки пользователь не видит.
"""
from __future__ import annotations

import logging
import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot import texts as t
from bot.config import settings
from bot.db.models import User
from bot.services.delivery import safe_call

log = logging.getLogger(__name__)
router = Router(name="support")
TAG = re.compile(r"#id(\d+)")


class Support(StatesGroup):
    text = State()


async def support_chat_id(sm: async_sessionmaker) -> int | None:
    """ID аккаунта поддержки: из SUPPORT_CHAT_ID или по @username среди тех, кто нажимал /start."""
    if settings.support_chat_id:
        return settings.support_chat_id
    if not settings.support_username:
        return None
    async with sm() as s:
        return (
            await s.execute(
                select(User.id).where(func.lower(User.username) == settings.support_username.lower()).limit(1)
            )
        ).scalar_one_or_none()


class FromSupport(Filter):
    async def __call__(self, message: Message, sm: async_sessionmaker) -> bool:
        if message.from_user is None or message.reply_to_message is None:
            return False
        return message.from_user.id == await support_chat_id(sm)


# ---------- ответ поддержки пользователю ----------

@router.message(FromSupport())
async def support_reply(message: Message, bot: Bot) -> None:
    original = message.reply_to_message
    match = TAG.search(original.text or original.caption or "")
    if not match or original.from_user is None or not original.from_user.is_bot:
        return  # ответ не на вопрос из поддержки: пусть обработают остальные роутеры
    user_id = int(match.group(1))
    body = message.html_text if message.text else escape(message.caption or "")
    if not body:
        await message.answer(t.SUPPORT_TEXT_ONLY)
        return
    res = await safe_call(
        lambda: bot.send_message(user_id, f"{t.SUPPORT_REPLY_TITLE}\n\n{body}", reply_markup=kb.main_kb()),
        what="support reply", user_id=user_id,
    )
    log.info("support reply to user=%s delivered=%s", user_id, res is not None)
    await message.reply(t.SUPPORT_REPLY_SENT if res is not None else t.SUPPORT_REPLY_FAILED)


# ---------- вопрос пользователя ----------

def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t.FB_CANCEL_BTN, callback_data="sup:cancel")]])


async def start_support(message: Message, state: FSMContext) -> None:
    await state.set_state(Support.text)
    await message.answer(t.SUPPORT_ASK, reply_markup=_cancel_kb())


@router.callback_query(F.data == "sup:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if await state.get_state() == Support.text.state:
        await state.set_state(None)
    try:
        await call.message.edit_text(t.FB_CANCELLED)
    except Exception:  # noqa: BLE001
        pass


@router.message(Support.text, F.text, ~F.text.startswith("/"), ~F.text.in_(t.MENU_BUTTONS))
async def on_question(message: Message, bot: Bot, state: FSMContext, sm: async_sessionmaker) -> None:
    await state.set_state(None)
    target = await support_chat_id(sm)
    user = message.from_user
    if target is None:
        log.warning("support account @%s has not started the bot, question from user %s is not delivered",
                    settings.support_username, user.id)
        await message.answer(t.SUPPORT_UNAVAILABLE, reply_markup=kb.main_kb())
        return
    async with sm() as s:
        db_user = await s.get(User, user.id)
    name = (db_user.full_name if db_user and db_user.full_name else None) or user.full_name
    who = escape(name or "")
    if user.username:
        who += f" (@{escape(user.username)})"
    text = (
        f"<b>Вопрос в поддержку</b>\n"
        f"От: {who}\n"
        f"#id{user.id}\n\n"
        f"{message.html_text}\n\n"
        "<i>Чтобы ответить, сделай свайп «Ответить» на этом сообщении.</i>"
    )
    res = await safe_call(lambda: bot.send_message(target, text), what="support question", user_id=target)
    log.info("support question from user=%s delivered=%s", user.id, res is not None)
    await message.answer(t.SUPPORT_SENT if res is not None else t.SUPPORT_UNAVAILABLE, reply_markup=kb.main_kb())


@router.message(Support.text, ~F.text)
async def on_non_text(message: Message) -> None:
    await message.answer(t.SUPPORT_TEXT_ONLY)
