"""Отзывы. Один сценарий для опроса после встречи и для кнопки «💬 Отзыв».

О встрече: оценка 1–5 (или «Не смог прийти») → что улучшить / что понравилось → пара слов → тема следующей
встречи. Любой шаг можно пропустить, сохраняется всё, что человек успел ответить.
О клубе: категория → текст.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import keyboards as kb
from bot import texts as t
from bot.db.models import Event
from bot.services import feedback as fb
from bot.services.delivery import Delivery

log = logging.getLogger(__name__)
router = Router(name="feedback")
# Команды и кнопки внизу во время опроса не считаем ответом
router.message.filter(~F.text.startswith("/"), ~F.text.in_(t.MENU_BUTTONS))


class FB(StatesGroup):
    tags = State()
    comment = State()
    next_topic = State()
    club_text = State()


async def _close_kb(call: CallbackQuery) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


async def _open_event(sm: async_sessionmaker, event_id: int) -> Event | None:
    async with sm() as s:
        ev = await s.get(Event, event_id)
    return ev if ev is not None and fb.is_open_for_feedback(ev) else None


# ---------- вход: кнопка «💬 Отзыв» ----------

async def start_feedback(bot: Bot, chat_id: int, user_id: int, state: FSMContext, sm: async_sessionmaker) -> None:
    await _clear_fb(state)
    event = await fb.event_for_feedback(sm, user_id)
    if event is None:
        await bot.send_message(chat_id, t.FB_CLUB_CATEGORY, reply_markup=kb.fb_club_kb(fb.CLUB_CATEGORIES))
        return
    await bot.send_message(chat_id, t.FB_CHOOSE, reply_markup=kb.fb_choose_kb(event.id, fb.event_button_label(event)))


async def _clear_fb(state: FSMContext) -> None:
    """Сбрасываем только незаконченный отзыв, чужие данные FSM (источник, режим без виджетов) не трогаем."""
    current = await state.get_state()
    if current and current.startswith("FB:"):
        await state.set_state(None)
    await state.update_data(fb_event=None, fb_tags=[], fb_score=None, fb_only_comment=False, fb_cat=None)


@router.callback_query(F.data.startswith("fb:ev:"))
async def cb_event(call: CallbackQuery, sm: async_sessionmaker) -> None:
    await call.answer()
    await _close_kb(call)
    event = await _open_event(sm, int(call.data.rsplit(":", 1)[1]))
    if event is None:
        await call.message.answer(t.FB_CLOSED)
        return
    existing = await fb.get_event_feedback(sm, call.from_user.id, event.id)
    if existing is not None and (existing.score is not None or existing.attended is False):
        await call.message.answer(t.fb_already_text(existing.score, existing.attended),
                                  reply_markup=kb.fb_already_kb(event.id))
        return
    await call.message.answer(t.fb_rate_text(event.title), reply_markup=kb.rating_kb(event.id))


@router.callback_query(F.data.startswith("fb:rerate:"))
async def cb_rerate(call: CallbackQuery, sm: async_sessionmaker) -> None:
    await call.answer()
    await _close_kb(call)
    event = await _open_event(sm, int(call.data.rsplit(":", 1)[1]))
    if event is None:
        await call.message.answer(t.FB_CLOSED)
        return
    await call.message.answer(t.fb_rate_text(event.title), reply_markup=kb.rating_kb(event.id))


@router.callback_query(F.data.startswith("fb:addc:"))
async def cb_add_comment(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker) -> None:
    await call.answer()
    await _close_kb(call)
    event = await _open_event(sm, int(call.data.rsplit(":", 1)[1]))
    if event is None:
        await call.message.answer(t.FB_CLOSED)
        return
    await state.update_data(fb_event=event.id, fb_only_comment=True)
    await state.set_state(FB.comment)
    await call.message.answer(t.FB_COMMENT_ONLY, reply_markup=kb.fb_skip_kb("comment"))


# ---------- шаг 1: оценка ----------

@router.callback_query(F.data.startswith("fb:r:"))
async def cb_rate(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker, delivery: Delivery) -> None:
    try:
        _, _, event_id_s, score_s = call.data.split(":")
        event_id, score = int(event_id_s), int(score_s)
    except ValueError:
        await call.answer()
        return
    if not 0 <= score <= 5:
        await call.answer()
        return
    event = await _open_event(sm, event_id)
    if event is None:
        await call.answer(t.FB_CLOSED, show_alert=True)
        return
    await call.answer()
    user_id = call.from_user.id
    await _ensure_user(sm, call)

    if score == 0:
        await fb.save_event_feedback(sm, user_id, event_id, attended=False, score=None)
        log.info("feedback user=%s event=%s absent", user_id, event_id)
        await _edit(call, t.fb_rate_text(event.title) + "\n\n<i>Не смог прийти</i>")
        await _clear_fb(state)
        await call.message.answer(t.FB_ABSENT_THANKS, reply_markup=kb.main_kb())
        return

    await fb.save_event_feedback(sm, user_id, event_id, attended=True, score=score)
    log.info("feedback user=%s event=%s score=%s", user_id, event_id, score)
    await _edit(call, t.fb_rate_text(event.title) + f"\n\n<i>Твоя оценка: {score} из 5</i>")
    await _sync_widget(sm, delivery, user_id, event_id)

    await state.update_data(fb_event=event_id, fb_score=score, fb_tags=[], fb_only_comment=False)
    await state.set_state(FB.tags)
    prompt = t.FB_IMPROVE if score <= 3 else t.FB_LIKED
    await call.message.answer(prompt, reply_markup=kb.fb_tags_kb(fb.tags_for(score), []))


async def _edit(call: CallbackQuery, text: str) -> None:
    try:
        await call.message.edit_text(text, reply_markup=None)
    except Exception:  # noqa: BLE001
        await _close_kb(call)


async def _ensure_user(sm: async_sessionmaker, call: CallbackQuery) -> None:
    from bot.services.registration import touch_user

    async with sm() as s, s.begin():
        await touch_user(s, call.from_user.id, call.from_user.username, call.from_user.first_name, None)


async def _sync_widget(sm: async_sessionmaker, delivery: Delivery, user_id: int, event_id: int) -> None:
    """Если у человека билет-виджет, показываем в нём поставленную оценку."""
    if delivery.widgets is None:
        return
    from bot.services.registration import get_registration

    async with sm() as s:
        reg = await get_registration(s, user_id, event_id)
    if reg is not None and reg.ticket_widget_msg_id and reg.ticket_widget_status == "done":
        await delivery.widgets.set_ticket_status(reg, "done")


# ---------- шаг 2: что улучшить / что понравилось ----------

@router.callback_query(F.data.startswith("fb:t:"), FB.tags)
async def cb_tag(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    options = fb.tags_for(data.get("fb_score"))
    codes = [c for c, _ in options]
    value = call.data.split(":", 2)[2]
    await call.answer()
    if value not in codes:
        return
    selected = list(data.get("fb_tags") or [])
    selected = [c for c in codes if (c in selected) != (c == value)]
    await state.update_data(fb_tags=selected)
    try:
        await call.message.edit_reply_markup(reply_markup=kb.fb_tags_kb(options, selected))
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.in_({"fb:td", "fb:ts"}), FB.tags)
async def cb_tags_done(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker) -> None:
    await call.answer()
    await _close_kb(call)
    data = await state.get_data()
    if call.data == "fb:td" and data.get("fb_tags"):
        await fb.save_event_feedback(sm, call.from_user.id, data["fb_event"], tags=list(data["fb_tags"]))
    await state.set_state(FB.comment)
    await call.message.answer(t.FB_COMMENT, reply_markup=kb.fb_skip_kb("comment"))


# ---------- шаг 3 и 4: текст ----------

async def _after_comment(message_or_call_answer, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("fb_only_comment"):
        await _finish(message_or_call_answer, state)
        return
    await state.set_state(FB.next_topic)
    await message_or_call_answer(t.FB_NEXT, reply_markup=kb.fb_skip_kb("next"))


async def _finish(answer, state: FSMContext) -> None:
    await _clear_fb(state)
    await answer(t.FB_THANKS, reply_markup=kb.main_kb())


def _text_ok(message: Message) -> str | None:
    value = (message.text or "").strip()
    return value if 0 < len(value) <= fb.MAX_TEXT else None


@router.message(FB.comment, F.text)
async def on_comment(message: Message, state: FSMContext, sm: async_sessionmaker) -> None:
    value = _text_ok(message)
    if value is None:
        await message.answer(t.FB_TOO_LONG)
        return
    data = await state.get_data()
    await fb.save_event_feedback(sm, message.from_user.id, data["fb_event"], comment=value)
    await _after_comment(message.answer, state)


@router.message(FB.next_topic, F.text)
async def on_next_topic(message: Message, state: FSMContext, sm: async_sessionmaker) -> None:
    value = _text_ok(message)
    if value is None:
        await message.answer(t.FB_TOO_LONG)
        return
    data = await state.get_data()
    await fb.save_event_feedback(sm, message.from_user.id, data["fb_event"], next_topic=value)
    await _finish(message.answer, state)


@router.callback_query(F.data == "fb:skip:comment", FB.comment)
async def cb_skip_comment(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _close_kb(call)
    await _after_comment(call.message.answer, state)


@router.callback_query(F.data == "fb:skip:next", FB.next_topic)
async def cb_skip_next(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _close_kb(call)
    await _finish(call.message.answer, state)


# ---------- о клубе в целом ----------

@router.callback_query(F.data == "fb:club")
async def cb_club(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _close_kb(call)
    await _clear_fb(state)
    await call.message.answer(t.FB_CLUB_CATEGORY, reply_markup=kb.fb_club_kb(fb.CLUB_CATEGORIES))


@router.callback_query(F.data.startswith("fbc:"))
async def cb_club_category(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _close_kb(call)
    code = call.data.split(":", 1)[1]
    if code == "cancel":
        await _clear_fb(state)
        await call.message.answer(t.FB_CANCELLED, reply_markup=kb.main_kb())
        return
    if code not in fb.CATEGORY_LABELS:
        return
    await state.update_data(fb_cat=code)
    await state.set_state(FB.club_text)
    await call.message.answer(t.FB_CLUB_TEXT)


@router.message(FB.club_text, F.text)
async def on_club_text(message: Message, state: FSMContext, sm: async_sessionmaker) -> None:
    value = _text_ok(message)
    if value is None:
        await message.answer(t.FB_TOO_LONG)
        return
    data = await state.get_data()
    await fb.add_club_feedback(sm, message.from_user.id, data.get("fb_cat") or "other", value)
    log.info("club feedback user=%s category=%s", message.from_user.id, data.get("fb_cat"))
    await _clear_fb(state)
    await message.answer(t.FB_CLUB_THANKS, reply_markup=kb.main_kb())


@router.message(FB.tags)
async def on_tags_text(message: Message) -> None:
    await message.answer(t.USE_BUTTONS)


# старые кнопки после окончания шага
@router.callback_query(F.data.startswith("fb:"))
async def cb_stale(call: CallbackQuery) -> None:
    await call.answer(t.FORM_EXPIRED)
