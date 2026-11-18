"""Админ-команды (только ADMIN_IDS): /stats, /export, /list, /link, /broadcast, /event, /event_set."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from datetime import datetime

from aiogram.filters import Command, CommandObject, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BOT_USERNAME, MSK, settings
from bot.db.models import Registration, User
from bot.services import admin as adm
from bot.services import registration as regs
from bot.services.delivery import SEND_DELAY, Delivery, safe_call
from bot.services.validation import parse_source

log = logging.getLogger(__name__)
router = Router(name="admin")

# Изменяемое множество: тесты добавляют сюда свой ID
ADMIN_IDS: set[int] = set(settings.admin_ids)


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in ADMIN_IDS


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# Кому доступен только /list (без остальной админки): LIST_VIEWER_IDS
LIST_VIEWER_IDS: set[int] = set(settings.list_viewer_ids)


class CanList(Filter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and (user.id in ADMIN_IDS or user.id in LIST_VIEWER_IDS)


list_router = Router(name="admin_list")
list_router.message.filter(CanList())


def _event_arg(command: CommandObject) -> int | None:
    arg = (command.args or "").strip()
    return int(arg) if arg.isdigit() else None


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    await message.answer(await adm.stats_text(sm, _event_arg(command)))


@router.message(Command("export"))
async def cmd_export(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    all_events = (command.args or "").strip().lower() == "all"
    data, name = await adm.export_csv(sm, None if all_events else _event_arg(command), all_events=all_events)
    if not name:
        await message.answer("Встреч нет.")
        return
    log.info("export by admin %s", message.from_user.id)
    await message.answer_document(BufferedInputFile(data, filename=name), caption="CSV: UTF-8, разделитель «;»")


@list_router.message(Command("list"))
async def cmd_list(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    header, lines = await adm.guard_list(sm, _event_arg(command))
    if not header:
        await message.answer("Встреч нет.")
        return
    log.info("list by %s", message.from_user.id)
    for chunk in adm.chunk_lines(header, lines):
        await message.answer(chunk.strip(), disable_web_page_preview=True)


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    code = parse_source(command.args)
    if code is None:
        await message.answer("Формат: /link код. Код: латиница, цифры, _ и -, до 64 символов.")
        return
    await message.answer(f"https://t.me/{BOT_USERNAME}?start={code}", disable_web_page_preview=True)


# ---------- встреча ----------

@router.message(Command("event"))
async def cmd_event(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    await message.answer(await adm.event_info(sm, _event_arg(command)), disable_web_page_preview=True)


@router.message(Command("event_set"))
async def cmd_event_set(message: Message, command: CommandObject, sm: async_sessionmaker, delivery: Delivery) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /event_set поле значение. Поля: " + ", ".join(adm.EDITABLE_FIELDS))
        return
    field, raw = parts
    try:
        value = adm.parse_field_value(field, raw)
    except adm.BadValue as e:
        await message.answer(str(e))
        return
    async with sm() as s:
        ev = await adm.admin_event(s)
    if ev is None:
        await message.answer("Встреч нет.")
        return
    await adm.set_event_field(sm, ev.id, field, value)
    log.info("admin %s set event %s %s", message.from_user.id, ev.id, field)
    extra = ""
    if field == "registration_limit":
        promoted = await regs.fill_waitlist(sm, ev.id)
        await delivery.notify_promoted(promoted)
        extra = f"\nИз листа ожидания получили места: {len(promoted)}"
    if field in ("venue_text", "starts_at"):
        extra = "\nКарточка-виджет будет создана заново при следующем /start. Уже отправленные билеты-виджеты показывают старые данные, PDF и текст берут новые."
    await delivery.seats_changed(ev.id)
    await message.answer("Сохранено." + extra + "\n\n" + await adm.event_info(sm, ev.id), disable_web_page_preview=True)


# ---------- рассылка ----------

class Broadcast(StatesGroup):
    mode = State()
    text = State()
    confirm = State()


MODES = {"org": "Организационное", "news": "Новости клуба"}


async def recipients(sm: async_sessionmaker, mode: str) -> list[int]:
    async with sm() as s:
        if mode == "org":
            ev = await adm.admin_event(s)
            if ev is None:
                return []
            q = select(Registration.user_id).where(Registration.event_id == ev.id, Registration.state == "registered")
        else:
            q = select(User.id).where(User.newsletter_opt_in.is_(True))
        return sorted(set((await s.execute(q)).scalars()))


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(Broadcast.mode)
    await message.answer(
        "Кому отправляем?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"{MODES['org']} (все registered текущей встречи)", callback_data="bc:org")],
                [InlineKeyboardButton(text=f"{MODES['news']} (только согласие на рассылку)", callback_data="bc:news")],
                [InlineKeyboardButton(text="Отмена", callback_data="bc:cancel")],
            ]
        ),
    )


@router.callback_query(F.data.in_({"bc:org", "bc:news"}), Broadcast.mode)
async def cb_bc_mode(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    mode = call.data.split(":")[1]
    await state.update_data(bc_mode=mode)
    await state.set_state(Broadcast.text)
    await call.message.edit_text(f"Режим: {MODES[mode]}. Пришли текст рассылки одним сообщением (форматирование сохранится).")


@router.message(Broadcast.text, F.text)
async def on_bc_text(message: Message, state: FSMContext, sm: async_sessionmaker) -> None:
    data = await state.get_data()
    mode = data.get("bc_mode", "org")
    body = message.html_text
    if mode == "news":
        body += "\n\n<i>Отписаться от новостей: /unsubscribe</i>"
    if len(body) > 4096:
        await message.answer("Слишком длинно, максимум около 4000 символов. Пришли короче.")
        return
    count = len(await recipients(sm, mode))
    await state.update_data(bc_body=body)
    await state.set_state(Broadcast.confirm)
    await message.answer("Превью ниже 👇")
    await message.answer(body, disable_web_page_preview=True)
    await message.answer(
        f"Режим: {MODES[mode]}. Получателей: {count}.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Отправить", callback_data="bc:send"),
                InlineKeyboardButton(text="Отмена", callback_data="bc:cancel"),
            ]]
        ),
    )


@router.callback_query(F.data == "bc:send", Broadcast.confirm)
async def cb_bc_send(call: CallbackQuery, bot: Bot, state: FSMContext, sm: async_sessionmaker) -> None:
    data = await state.get_data()
    await state.clear()  # сразу: второй тап по «Отправить» уже не пройдёт фильтр состояния
    await call.answer()
    await call.message.edit_text(call.message.text + "\n\nОтправляю…")
    ids = await recipients(sm, data.get("bc_mode", "org"))
    body = data.get("bc_body", "")
    log.info("broadcast by admin %s mode=%s to %s users", call.from_user.id, data.get("bc_mode"), len(ids))

    async def run() -> None:
        ok = 0
        for uid in ids:
            res = await safe_call(
                lambda: bot.send_message(uid, body, disable_web_page_preview=True), what="broadcast", user_id=uid
            )
            ok += res is not None
            await asyncio.sleep(SEND_DELAY)
        await bot.send_message(call.from_user.id, f"Рассылка завершена: доставлено {ok} из {len(ids)}.")

    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


_BACKGROUND: set[asyncio.Task] = set()


@router.callback_query(F.data == "bc:cancel")
async def cb_bc_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text("Рассылка отменена.")


@router.callback_query(F.data.startswith("bc:"))
async def cb_bc_stale(call: CallbackQuery) -> None:
    await call.answer("Эта кнопка уже не активна.")


# ---------- отзывы ----------

@router.message(Command("feedback"))
async def cmd_feedback(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    data, name = await adm.export_feedback_csv(sm, _event_arg(command))
    log.info("feedback export by admin %s", message.from_user.id)
    await message.answer_document(
        BufferedInputFile(data, filename=name),
        caption="Отзывы: о встречах и о клубе. CSV для Excel, разделитель «;». /feedback <id встречи> — только одна встреча.",
    )


# ---------- новая встреча ----------

class EventNew(StatesGroup):
    title = State()
    speaker = State()
    starts_at = State()
    ends_at = State()
    venue_text = State()
    venue_url = State()
    seats = State()
    limit = State()
    confirm = State()


NEW_STEPS = [
    (EventNew.title, "Шаг 1 из 8. Название встречи, например: <i>Встреча с Анной Ивановой</i>"),
    (EventNew.speaker, "Шаг 2 из 8. Спикер полностью: имя и кто он, например: <i>Анна Иванова, CEO компании «Х»</i>"),
    (EventNew.starts_at, "Шаг 3 из 8. Дата и время начала по Москве: <i>ДД.ММ.ГГГГ ЧЧ:ММ</i>"),
    (EventNew.ends_at, "Шаг 4 из 8. Время окончания: <i>ЧЧ:ММ</i>. Опрос участникам уйдёт через 30 минут после него"),
    (EventNew.venue_text, "Шаг 5 из 8. Место: название площадки и адрес"),
    (EventNew.venue_url, "Шаг 6 из 8. Ссылка на карту (https://…) или «-», если не нужна"),
    (EventNew.seats, "Шаг 7 из 8. Сколько мест в зале"),
    (EventNew.limit, "Шаг 8 из 8. Сколько регистраций принимать (с запасом на неявку) или «-»: тогда мест × 1,25"),
]
CANCEL_WORDS = {"отмена", "стоп", "cancel"}


def _new_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="en:cancel")]])


async def _ask_new(message: Message, state: FSMContext, idx: int) -> None:
    st, prompt = NEW_STEPS[idx]
    await state.set_state(st)
    await message.answer(prompt, reply_markup=_new_cancel_kb())


@router.message(Command("event_new"))
async def cmd_event_new(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(en={})
    await message.answer("<b>Новая встреча</b>\n\nОтвечай по шагам. Отменить можно в любой момент кнопкой или словом «отмена».")
    await _ask_new(message, state, 0)


@router.callback_query(F.data == "en:cancel")
async def cb_event_new_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    current = await state.get_state()
    if current and current.startswith("EventNew:"):
        await state.set_state(None)
    await call.message.answer("Создание встречи отменено.")


def _parse_new(st: State, raw: str, data: dict):
    raw = raw.strip()
    if st == EventNew.title:
        if not 3 <= len(raw) <= 120:
            raise adm.BadValue("Название: от 3 до 120 символов")
        return raw
    if st == EventNew.speaker:
        if not 3 <= len(raw) <= 300:
            raise adm.BadValue("Спикер: от 3 до 300 символов")
        return raw
    if st == EventNew.starts_at:
        value = adm.parse_field_value("starts_at", raw)
        if value <= regs.utcnow():
            raise adm.BadValue("Это время уже прошло")
        return value
    if st == EventNew.ends_at:
        try:
            end_t = datetime.strptime(raw, "%H:%M").time()
        except ValueError as e:
            raise adm.BadValue("Время окончания: ЧЧ:ММ, например 20:00") from e
        start = data["starts_at"].astimezone(MSK)
        end = datetime.combine(start.date(), end_t, tzinfo=MSK)
        if end <= start:
            raise adm.BadValue("Окончание должно быть позже начала")
        return end
    if st == EventNew.venue_text:
        return adm.parse_field_value("venue_text", raw)
    if st == EventNew.venue_url:
        return adm.parse_field_value("venue_url", raw)
    if st == EventNew.seats:
        if not raw.isdigit() or not 1 <= int(raw) <= 10000:
            raise adm.BadValue("Число мест: целое число от 1")
        return int(raw)
    if st == EventNew.limit:
        if raw in ("-", "—"):
            return round(data["seats"] * 1.25)
        if not raw.isdigit() or int(raw) < data["seats"]:
            raise adm.BadValue(f"Лимит регистраций: целое число не меньше {data['seats']} (мест в зале)")
        return int(raw)
    raise adm.BadValue("unknown step")


@router.message(StateFilter(*[st for st, _ in NEW_STEPS]), F.text)
async def on_event_new_step(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() in CANCEL_WORDS:
        await state.set_state(None)
        await message.answer("Создание встречи отменено.")
        return
    current = await state.get_state()
    idx = next(i for i, (st, _) in enumerate(NEW_STEPS) if st.state == current)
    st = NEW_STEPS[idx][0]
    data = dict((await state.get_data()).get("en") or {})
    restored = {k: datetime.fromisoformat(v) if k in ("starts_at", "ends_at") else v for k, v in data.items()}
    try:
        value = _parse_new(st, message.text, restored)
    except adm.BadValue as e:
        await message.answer(str(e))
        return
    key = st.state.split(":", 1)[1]
    key = "registration_limit" if key == "limit" else key
    data[key] = value.isoformat() if isinstance(value, datetime) else value
    await state.update_data(en=data)
    if idx + 1 < len(NEW_STEPS):
        await _ask_new(message, state, idx + 1)
        return
    await state.set_state(EventNew.confirm)
    start = datetime.fromisoformat(data["starts_at"]).astimezone(MSK)
    end = datetime.fromisoformat(data["ends_at"]).astimezone(MSK)
    from html import escape

    await message.answer(
        "<b>Проверь встречу</b>\n\n"
        f"<b>Название:</b> {escape(data['title'])}\n"
        f"<b>Спикер:</b> {escape(data['speaker'])}\n"
        f"<b>Когда:</b> {start:%d.%m.%Y}, {start:%H:%M}–{end:%H:%M} МСК\n"
        f"<b>Где:</b> {escape(data['venue_text'])}\n"
        f"<b>Карта:</b> {escape(data.get('venue_url') or '—')}\n"
        f"<b>Мест:</b> {data['seats']}, регистраций: {data['registration_limit']}\n\n"
        "После создания регистрация откроется сразу. Бот показывает ближайшую встречу, поэтому эту участники "
        "увидят, когда пройдут более ранние.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Создать", callback_data="en:create"),
            InlineKeyboardButton(text="Отмена", callback_data="en:cancel"),
        ]]),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "en:create", EventNew.confirm)
async def cb_event_new_create(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker) -> None:
    data = dict((await state.get_data()).get("en") or {})
    await state.set_state(None)  # сразу: второй тап по «Создать» уже не пройдёт фильтр состояния
    await call.answer()
    for k in ("starts_at", "ends_at"):
        data[k] = datetime.fromisoformat(data[k])
    ev = await adm.create_event(sm, data)
    log.info("admin %s created event %s", call.from_user.id, ev.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await call.message.answer(
        f"Встреча создана, id {ev.id}.\n\nНапоминания и опрос после неё будут отправлены автоматически.\n\n"
        + await adm.event_info(sm, ev.id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "en:create")
async def cb_event_new_stale(call: CallbackQuery) -> None:
    await call.answer("Эта кнопка уже не активна.")
