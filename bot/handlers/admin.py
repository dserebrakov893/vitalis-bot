"""Админ-команды (только ADMIN_IDS): /stats, /export, /list, /link, /broadcast, /event, /event_set."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BOT_USERNAME, settings
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


@router.message(Command("list"))
async def cmd_list(message: Message, command: CommandObject, sm: async_sessionmaker) -> None:
    header, lines = await adm.guard_list(sm, _event_arg(command))
    if not header:
        await message.answer("Встреч нет.")
        return
    for chunk in adm.chunk_lines(header, lines):
        await message.answer(chunk)


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
