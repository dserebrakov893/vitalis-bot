from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot import texts as t


def _ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# Поднимите версию при смене кнопок: бот заново поставит клавиатуру тем, у кого старая
MAIN_KB_VERSION = 2


def main_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу чата."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t.MENU_EVENT), KeyboardButton(text=t.MENU_TICKET)],
            [KeyboardButton(text=t.MENU_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери кнопку внизу",
    )


def card_kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_ib(t.BTN_GO, f"go:{event_id}")]])


def back_row(step: str) -> list[InlineKeyboardButton]:
    return [_ib(t.BTN_BACK, f"f:{step}:back")]


def back_only(step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[back_row(step)])


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t.BTN_SHARE_PHONE, request_contact=True)], [KeyboardButton(text=t.BTN_BACK)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=True,
    )


def options_kb(step: str, options: list[tuple[str, str]], extra: list[tuple[str, str]] = (), columns: int = 1) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons = [_ib(label, f"f:{step}:{code}") for code, label in [*options, *extra]]
    for i in range(0, len(buttons), columns):
        rows.append(buttons[i : i + columns])
    rows.append(back_row(step))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def interests_kb(selected: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [_ib(("✓ " if code in selected else "") + label, f"f:interests:{code}")] for code, label in t.INTERESTS
    ]
    rows.append([_ib(t.BTN_DONE, "f:interests:done")])
    rows.append(back_row("interests"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def consent_pdn_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t.BTN_ADULT, "f:pdn:adult")],
            [_ib(t.BTN_MINOR, "f:pdn:17_with_parent")],
            back_row("pdn"),
        ]
    )


def consent_news_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ib(t.BTN_NEWS_YES, "f:news:yes")], [_ib(t.BTN_NEWS_NO, "f:news:no")], back_row("news")]
    )


def ticket_kb(event_id: int, calendar_url: str, venue_url: str | None) -> InlineKeyboardMarkup:
    first = [InlineKeyboardButton(text=t.BTN_CALENDAR, url=calendar_url)]
    if venue_url:
        first.insert(0, InlineKeyboardButton(text=t.BTN_MAP, url=venue_url))
    return InlineKeyboardMarkup(inline_keyboard=[first, [_ib(t.BTN_CANT_COME, f"cx:ask:{event_id}")]])


def cancel_confirm_kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ib(t.BTN_CANCEL_YES, f"cx:yes:{event_id}"), _ib(t.BTN_CANCEL_NO, "cx:no")]]
    )


def delete_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_ib(t.BTN_DELETE_YES, "del:yes"), _ib(t.BTN_DELETE_NO, "del:no")]])


def rating_kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_ib(str(i), f"rate:{event_id}:{i}") for i in range(1, 6)]])


def reminder_kb(event_id: int, venue_url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if venue_url:
        rows.append([InlineKeyboardButton(text=t.BTN_MAP, url=venue_url)])
    rows.append([_ib(t.BTN_CANT_COME, f"cx:ask:{event_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
