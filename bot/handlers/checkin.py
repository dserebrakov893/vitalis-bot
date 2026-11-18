"""Бот-часть чекина: /checkers (приглашения проверяющих), /checkin (открыть Mini App), вход по ссылке."""
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BOT_USERNAME, MSK, settings
from bot.services import checkin

log = logging.getLogger(__name__)
router = Router(name="checkin")
INVITE_PREFIX = "chk_"


def _admins() -> set[int]:
    from bot.handlers.admin import ADMIN_IDS

    return ADMIN_IDS


def open_button() -> InlineKeyboardButton | None:
    if not settings.webapp_url:
        return None
    return InlineKeyboardButton(text="Открыть чекин", web_app=WebAppInfo(url=f"{settings.webapp_url}/checkin"))


def _open_kb() -> InlineKeyboardMarkup | None:
    btn = open_button()
    return InlineKeyboardMarkup(inline_keyboard=[[btn]]) if btn else None


def no_url_text() -> str:
    raw = settings.webapp_url_raw
    problem = f"WEBAPP_URL задан неправильно: «{escape(raw)}»." if raw else "Не задан WEBAPP_URL."
    return (
        f"Mini App чекина не настроен. {problem}\n\n"
        "В Railway: vitalis-bot → Settings → Networking → скопируй домен (или нажми Generate Domain, порт 8080). "
        "Затем в Variables: WEBAPP_URL=https://этот-домен, например https://vitalis-bot-production.up.railway.app"
    )


@router.message(Command("checkers"))
async def cmd_checkers(message: Message, sm: async_sessionmaker) -> None:
    if message.from_user.id not in _admins():
        return
    event = await checkin.checkin_event(sm)
    if event is None:
        await message.answer("Встреч для чекина нет.")
        return
    token = await checkin.create_invite(sm, event, message.from_user.id)
    log.info("admin %s created checker invite for event %s", message.from_user.id, event.id)
    active = await checkin.list_checkers(sm, event)
    expires = checkin.checker_expires(event).astimezone(MSK)
    lines = [
        f"<b>Проверяющие: {escape(event.title)}</b>",
        "",
        "Ссылка-приглашение (одноразовая, на одного человека):",
        f"https://t.me/{BOT_USERNAME}?start={INVITE_PREFIX}{token}",
        "",
        f"Доступ действует до {expires:%d.%m %H:%M} МСК и отключится сам.",
        "Проверяющий видит только ФИО и номер билета.",
        "",
        "Сейчас с доступом: " + (", ".join(escape(n) for _, n in active) if active else "никого"),
    ]
    if not settings.webapp_url:
        lines += ["", no_url_text()]
    rows = []
    btn = open_button()
    if btn:
        rows.append([btn])
    if active:
        rows.append([InlineKeyboardButton(text="Отозвать доступ у всех", callback_data=f"chk:revoke:{event.id}")])
    await message.answer("\n".join(lines), disable_web_page_preview=True,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("chk:revoke:"))
async def cb_revoke(call: CallbackQuery, sm: async_sessionmaker) -> None:
    if call.from_user.id not in _admins():
        await call.answer()
        return
    from bot.db.models import Event

    async with sm() as s:
        event = await s.get(Event, int(call.data.rsplit(":", 1)[1]))
    n = await checkin.revoke_all(sm, event) if event else 0
    log.info("admin %s revoked %s checkers", call.from_user.id, n)
    await call.answer(f"Доступ отозван: {n}", show_alert=True)


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, sm: async_sessionmaker) -> None:
    event = await checkin.access_event(sm, message.from_user.id, _admins())
    if event is None:
        return  # не проверяющий: команда для него не существует
    if not settings.webapp_url:
        await message.answer(no_url_text())
        return
    came, total = await checkin.counts(sm, event.id)
    await message.answer(f"<b>Чекин: {escape(event.title)}</b>\n\nПришло {came} из {total}.", reply_markup=_open_kb())


async def redeem_from_start(message: Message, sm: async_sessionmaker, token: str) -> None:
    from bot.services.registration import touch_user

    user = message.from_user
    async with sm() as s, s.begin():
        # чтобы в /checkers было видно @username проверяющего; источник регистрации не пишем
        await touch_user(s, user.id, user.username, user.first_name, None)
    res = await checkin.redeem_invite(sm, token, user.id)
    if not res.ok:
        text = {
            "used": "Эта ссылка уже использована. Попроси организатора прислать новую.",
            "expired": "Ссылка устарела. Попроси организатора прислать новую.",
        }.get(res.reason, "Ссылка недействительна. Попроси организатора прислать новую.")
        await message.answer(text)
        return
    log.info("user %s became checker for event %s", message.from_user.id, res.event.id)
    expires = checkin.checker_expires(res.event).astimezone(MSK)
    text = (
        f"<b>Готово, ты проверяющий на встрече «{escape(res.event.title)}»</b>\n\n"
        "Открой чекин кнопкой ниже: сканируй QR-код из билета или ищи гостя по фамилии.\n"
        f"Доступ действует до {expires:%d.%m %H:%M} МСК. Открыть снова: /checkin"
    )
    await message.answer(text, reply_markup=_open_kb())
