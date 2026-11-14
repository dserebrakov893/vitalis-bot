"""Анкета регистрации: 9 шагов, «Назад» на каждом, состояние в PostgreSQL."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot import texts as t
from bot.config import settings
from bot.db.models import Event
from bot.services import registration as regs
from bot.services.delivery import Delivery
from bot.services.validation import (
    is_own_contact,
    normalize_phone,
    validate_email,
    validate_full_name,
    validate_university,
)

log = logging.getLogger(__name__)
router = Router(name="form")
# Команды (/cancel, /about, …) во время анкеты не считаем ответом на шаг
router.message.filter(~F.text.startswith("/"), ~F.text.in_(t.MENU_BUTTONS))


class Form(StatesGroup):
    full_name = State()
    phone = State()
    email = State()
    university = State()
    university_other = State()
    status = State()
    interests = State()
    pdn = State()
    news = State()


BY_NAME = {s.state.split(":", 1)[1]: s for s in Form.__all_states__}
PREV = {
    "phone": "full_name",
    "email": "phone",
    "university": "email",
    "university_other": "university",
    "status": "university",
    "interests": "status",
    "pdn": "interests",
    "news": "pdn",
}
UNI_LABELS = dict(t.UNIVERSITIES)
STATUS_CODES = {code for code, _ in t.STATUSES}
INTEREST_CODES = [code for code, _ in t.INTERESTS]


async def ask(bot: Bot, chat_id: int, state: FSMContext, step: State) -> None:
    await state.set_state(step)
    name = step.state.split(":", 1)[1]
    data = await state.get_data()
    if name == "full_name":
        await bot.send_message(chat_id, t.ASK_FULL_NAME, reply_markup=kb.back_only(name))
    elif name == "phone":
        await bot.send_message(chat_id, t.ASK_PHONE, reply_markup=kb.phone_kb())
    elif name == "email":
        await bot.send_message(chat_id, t.ASK_EMAIL, reply_markup=kb.back_only(name))
    elif name == "university":
        await bot.send_message(
            chat_id, t.ASK_UNIVERSITY,
            reply_markup=kb.options_kb(name, t.UNIVERSITIES, extra=[("other", t.UNI_OTHER)]),
        )
    elif name == "university_other":
        await bot.send_message(chat_id, t.ASK_UNIVERSITY_OTHER, reply_markup=kb.back_only(name))
    elif name == "status":
        await bot.send_message(chat_id, t.ASK_STATUS, reply_markup=kb.options_kb(name, t.STATUSES, columns=2))
    elif name == "interests":
        await bot.send_message(chat_id, t.ASK_INTERESTS, reply_markup=kb.interests_kb(data.get("interests", [])))
    elif name == "pdn":
        if not settings.privacy_policy_url:
            log.warning("PRIVACY_POLICY_URL is empty: consent is shown without a link")
        await bot.send_message(
            chat_id, t.consent_pdn_html(settings.privacy_policy_url),
            reply_markup=kb.consent_pdn_kb(), disable_web_page_preview=True,
        )
    elif name == "news":
        await bot.send_message(chat_id, t.consent_news_html(), reply_markup=kb.consent_news_kb())


async def _go_back(bot: Bot, chat_id: int, state: FSMContext, step: str, sm, delivery) -> None:
    if step == "full_name":
        # назад с первого шага: выходим из анкеты к карточке встречи
        data = await state.get_data()
        await state.set_state(None)
        async with sm() as s:
            event = await s.get(Event, data.get("event_id")) if data.get("event_id") else None
        if event is not None:
            await delivery.send_event_card(chat_id, event, prefer_widgets=not data.get("fallback", False))
        return
    await ask(bot, chat_id, state, BY_NAME[PREV[step]])


async def _current_step(state: FSMContext) -> str | None:
    current = await state.get_state()
    if not current or not current.startswith("Form:"):
        return None
    return current.split(":", 1)[1]


# ---------- текстовые шаги ----------

@router.message(Form.full_name, F.text)
async def on_full_name(message: Message, bot: Bot, state: FSMContext) -> None:
    name = validate_full_name(message.text)
    if name is None:
        await message.answer(t.BAD_FULL_NAME, reply_markup=kb.back_only("full_name"))
        return
    await state.update_data(full_name=name)
    await ask(bot, message.chat.id, state, Form.phone)


@router.message(Form.phone, F.contact)
async def on_contact(message: Message, bot: Bot, state: FSMContext) -> None:
    if not is_own_contact(message.contact.user_id, message.from_user.id):
        log.info("foreign contact rejected for user %s", message.from_user.id)
        await message.answer(t.BAD_PHONE, reply_markup=kb.phone_kb())
        return
    await state.update_data(phone=normalize_phone(message.contact.phone_number))
    await message.answer(t.PHONE_SAVED, reply_markup=kb.main_kb())
    await ask(bot, message.chat.id, state, Form.email)


@router.message(Form.phone, F.text == t.BTN_BACK)
async def on_phone_back(message: Message, bot: Bot, state: FSMContext) -> None:
    await message.answer(t.PHONE_BACK, reply_markup=kb.main_kb())
    await ask(bot, message.chat.id, state, Form.full_name)


@router.message(Form.phone)
async def on_phone_other(message: Message) -> None:
    await message.answer(t.BAD_PHONE, reply_markup=kb.phone_kb())


@router.message(Form.email, F.text)
async def on_email(message: Message, bot: Bot, state: FSMContext) -> None:
    email = validate_email(message.text)
    if email is None:
        await message.answer(t.BAD_EMAIL, reply_markup=kb.back_only("email"))
        return
    await state.update_data(email=email)
    await ask(bot, message.chat.id, state, Form.university)


@router.message(Form.university_other, F.text)
async def on_university_other(message: Message, bot: Bot, state: FSMContext) -> None:
    uni = validate_university(message.text)
    if uni is None:
        await message.answer(t.BAD_UNIVERSITY, reply_markup=kb.back_only("university_other"))
        return
    await state.update_data(university=uni)
    await ask(bot, message.chat.id, state, Form.status)


@router.message(StateFilter(Form.full_name, Form.email, Form.university_other))
async def on_text_expected(message: Message, state: FSMContext) -> None:
    await message.answer(t.TEXT_EXPECTED)


@router.message(StateFilter(Form.university, Form.status, Form.interests, Form.pdn, Form.news))
async def on_buttons_expected(message: Message) -> None:
    await message.answer(t.USE_BUTTONS)


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("f:"))
async def on_form_button(call: CallbackQuery, bot: Bot, state: FSMContext, sm: async_sessionmaker,
                         delivery: Delivery) -> None:
    try:
        _, step, value = call.data.split(":", 2)
    except ValueError:
        await call.answer()
        return
    if await _current_step(state) != step:
        # кнопка из старого сообщения или повторный тап
        await call.answer(t.FORM_EXPIRED)
        return
    chat_id = call.message.chat.id

    async def close_kb() -> None:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 — сообщение могло быть уже изменено
            pass

    if value == "back":
        await call.answer()
        await close_kb()
        await _go_back(bot, chat_id, state, step, sm, delivery)
        return

    if step == "university":
        await call.answer()
        await close_kb()
        if value == "other":
            await ask(bot, chat_id, state, Form.university_other)
        elif value in UNI_LABELS:
            await state.update_data(university=UNI_LABELS[value])
            await ask(bot, chat_id, state, Form.status)
        return

    if step == "status" and value in STATUS_CODES:
        await call.answer()
        await close_kb()
        await state.update_data(status=value)
        await ask(bot, chat_id, state, Form.interests)
        return

    if step == "interests":
        selected: list[str] = list((await state.get_data()).get("interests", []))
        if value == "done":
            if not selected:
                await call.answer(t.NEED_INTEREST, show_alert=True)
                return
            await call.answer()
            await close_kb()
            await ask(bot, chat_id, state, Form.pdn)
            return
        if value in INTEREST_CODES:
            selected = [c for c in INTEREST_CODES if (c in selected) != (c == value)]
            await state.update_data(interests=selected)
            await call.answer()
            try:
                await call.message.edit_reply_markup(reply_markup=kb.interests_kb(selected))
            except Exception:  # noqa: BLE001
                pass
        return

    if step == "pdn" and value in ("adult", "17_with_parent"):
        await call.answer()
        await close_kb()
        await state.update_data(age_group=value)
        await ask(bot, chat_id, state, Form.news)
        return

    if step == "news" and value in ("yes", "no"):
        await call.answer()
        await close_kb()
        await _finish(bot, call, state, sm, delivery, newsletter=value == "yes")
        return

    await call.answer()


async def _finish(bot: Bot, call: CallbackQuery, state: FSMContext, sm: async_sessionmaker, delivery: Delivery,
                  newsletter: bool) -> None:
    data = await state.get_data()
    # Сразу выходим из анкеты: повторный тап по кнопке уже не пройдёт проверку шага
    await state.set_state(None)
    required = ("event_id", "full_name", "phone", "email", "university", "status", "age_group")
    if any(not data.get(k) for k in required) or not data.get("interests"):
        log.warning("incomplete form for user %s", call.from_user.id)
        await bot.send_message(call.message.chat.id, t.FORM_BROKEN)
        return
    profile = regs.Profile(
        full_name=data["full_name"],
        phone=data["phone"],
        email=data["email"],
        university=data["university"],
        status=data["status"],
        interests=data["interests"],
        project_stage=None,  # шаг убран из анкеты 14.11.2026, колонка остаётся
        age_group=data["age_group"],
        newsletter_opt_in=newsletter,
    )
    try:
        result = await regs.complete_registration(sm, call.from_user.id, data["event_id"], profile, data.get("source"))
    except regs.RegistrationClosed:
        await bot.send_message(call.message.chat.id, t.REG_CLOSED)
        return
    # ПДн из FSM больше не нужны: оставляем только служебные флаги
    await state.set_data({"source": data.get("source"), "fallback": data.get("fallback", False)})
    log.info("registered user=%s event=%s state=%s created=%s", call.from_user.id, data["event_id"],
             result.registration.state, result.created)
    if result.created:
        await delivery.after_registration(result, prefer_widgets=not data.get("fallback", False))
