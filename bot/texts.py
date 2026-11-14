"""Все тексты бота. Обращение на «ты», не больше одного эмодзи на сообщение.

Тексты согласий (CONSENT_*) менять нельзя: при изменении поднимите версию в services/registration.py.
"""
from html import escape

from bot.config import JBD_SITE, OPERATOR_EMAIL

BOT_NAME = "Vitalis | регистрация"
BOT_ABOUT = "Регистрация на встречи бизнес-клуба Vitalis, билеты и напоминания. Сделано JBD: justbusinessdarling.com"
BOT_DESCRIPTION = (
    "Vitalis объединяет студентов, исследователей и предпринимателей, чтобы у российской медицины "
    "и биотеха были свои сильные компании.\n\n"
    "Здесь вы:\n"
    "• регистрируетесь на встречи клуба\n"
    "• получаете именной билет\n"
    "• получаете напоминания и адрес встречи\n\n"
    "Анонсы и материалы в канале клуба, общение в чате."
)
COMMANDS = [
    ("start", "Ближайшая встреча"),
    ("ticket", "Мой билет"),
    ("cancel", "Не смогу прийти"),
    # /club и /about временно убраны (14.11.2026): нет ссылок на канал и чат, текст «О клубе» на согласовании
]

# --- согласия (дословно) ---
CONSENT_PDN = (
    "Я соглашаюсь на обработку моих персональных данных, включая их трансграничную передачу через Telegram "
    "для регистрации на встречи клуба Vitalis на условиях Политики. Если мне 17 лет, подтверждаю, "
    "что мой законный представитель согласен."
)
CONSENT_NEWSLETTER = (
    "Хочу получать новости и анонсы клуба Vitalis на почту и в этом боте. Отписаться можно в любой момент."
)


def consent_pdn_html(policy_url: str) -> str:
    text = escape(CONSENT_PDN)
    if policy_url:
        text = text.replace("Политики", f'<a href="{escape(policy_url, quote=True)}">Политики</a>', 1)
    # заголовок шага сверху, сам текст согласия без изменений
    return f"{CONSENT_PDN_TITLE}\n\n<blockquote>{text}</blockquote>"


def consent_news_html() -> str:
    return f"{CONSENT_NEWS_TITLE}\n\n<blockquote>{escape(CONSENT_NEWSLETTER)}</blockquote>"


BTN_GO = "Иду"
# постоянная клавиатура внизу чата
MENU_EVENT = "📅 Ближайшая встреча"
MENU_TICKET = "🎟 Мой билет"
MENU_CANCEL = "🚫 Не смогу прийти"
# старые подписи без эмодзи: у тех, кто уже видел клавиатуру, она остаётся до замены
MENU_EVENT_ALIASES = (MENU_EVENT, "Ближайшая встреча")
MENU_TICKET_ALIASES = (MENU_TICKET, "Мой билет")
MENU_CANCEL_ALIASES = (MENU_CANCEL, "Не смогу прийти")
MENU_BUTTONS = MENU_EVENT_ALIASES + MENU_TICKET_ALIASES + MENU_CANCEL_ALIASES
BTN_BACK = "← Назад"
BTN_ADULT = "Мне 18+, согласен"
BTN_MINOR = "Мне 17, родитель согласен"
BTN_NEWS_YES = "Да, хочу"
BTN_NEWS_NO = "Только билет и напоминания"
BTN_SHARE_PHONE = "Отправить мой номер"
BTN_DONE = "Готово"
BTN_CALENDAR = "В календарь"
BTN_CANT_COME = "Не смогу прийти"
BTN_CANCEL_YES = "Да, не смогу"
BTN_CANCEL_NO = "Нет, приду"
BTN_DELETE_YES = "Да, удалить"
BTN_DELETE_NO = "Нет"
BTN_MAP = "Адрес на карте"

UNIVERSITIES = [
    ("sech", "Сеченовский"),
    ("rnimu", "Пироговский (РНИМУ)"),
    ("mgmsu", "МГМСУ"),
    ("rudn", "РУДН (мед. институт)"),
    ("msu", "МГУ (ФФМ)"),
]
UNI_OTHER = "Другой"
STATUSES = [("student", "Студент"), ("resident", "Ординатор"), ("doctor", "Врач"), ("other", "Не медик")]
INTERESTS = [
    ("private_practice", "Частная практика"),
    ("medtech", "Медтех и стартапы"),
    ("pharma_biotech", "Фарма и биотех"),
    ("undecided", "Пока не знаю"),
]
PROJECT_STAGES = [("running", "Уже работает"), ("idea", "Есть идея"), ("none", "Пока нет")]

# --- анкета ---
# Шагов 8: ФИО, телефон, почта, вуз, статус, интересы, согласие на ПДн, согласие на новости
STEPS = 8


def step(n: int, title: str) -> str:
    return f"<b>Шаг {n} из {STEPS}. {title}</b>"


ASK_FULL_NAME = (
    step(1, "Как тебя зовут?") + "\n\n"
    "Напиши фамилию, имя и отчество, как в паспорте. По этому списку пускают на площадку."
)
BAD_FULL_NAME = (
    "Не получилось разобрать имя.\n\n"
    "Нужно 2–3 слова, только буквы и дефис. Например: <i>Иванова Анна Сергеевна</i>"
)
ASK_PHONE = (
    step(2, "Телефон") + "\n\n"
    "Нажми кнопку «Отправить мой номер» внизу. Вводить вручную не нужно."
)
BAD_PHONE = "Нужен твой собственный номер.\n\nНажми кнопку «Отправить мой номер» внизу экрана."
PHONE_SAVED = "Номер сохранили."
PHONE_BACK = "Хорошо, вернёмся на шаг назад."
ASK_EMAIL = step(3, "Почта") + "\n\nНапиши адрес электронной почты."
BAD_EMAIL = "Похоже, в адресе опечатка.\n\nПроверь и пришли ещё раз. Например: <i>anna@mail.ru</i>"
ASK_UNIVERSITY = step(4, "Где учишься или учился?")
ASK_UNIVERSITY_OTHER = step(4, "Вуз") + "\n\nНапиши название своего вуза."
BAD_UNIVERSITY = "Название должно быть от 2 до 100 символов."
ASK_STATUS = step(5, "Кто ты сейчас?")
ASK_INTERESTS = (
    step(6, "Что тебе интересно?") + "\n\n"
    "Можно выбрать несколько вариантов, потом нажми «Готово»."
)
NEED_INTEREST = "Выбери хотя бы один пункт"
CONSENT_PDN_TITLE = step(7, "Согласие на обработку данных")
CONSENT_NEWS_TITLE = step(8, "Новости клуба")
USE_BUTTONS = "Выбери вариант кнопкой выше."
TEXT_EXPECTED = "Пришли ответ текстом."
FORM_EXPIRED = "Эта кнопка уже не активна."
FORM_BROKEN = "Что-то пошло не так.\n\nНажми «Ближайшая встреча» внизу и заполни анкету ещё раз."

# --- регистрация ---
NO_EVENTS = "Ближайших встреч пока нет.\n\nКак только откроем регистрацию, бот напишет."
REG_CLOSED = "Регистрация на эту встречу закрыта."
WIDGET_CAPTION_HINT = "Не видишь карточку? Обнови Telegram или нажми /reg"
MAIN_HINT = (
    "Не понял сообщение.\n\n"
    "Нажми «Ближайшая встреча» внизу, чтобы открыть встречу, или «Мой билет», чтобы увидеть билет."
)


def registered_text(name: str, ticket_no: int, with_file: bool = True) -> str:
    file_part = " Ещё мы продублировали его файлом." if with_file else ""
    return (
        f"<b>Готово, {escape(name)}, ты в списке!</b>\n\n"
        f"Твой билет №{ticket_no} ниже.{file_part}\n"
        "Напомним о встрече за день и за 2 часа до начала."
    )


def waitlist_text(position: int) -> str:
    return (
        "<b>Мест пока нет, но ты в листе ожидания</b>\n\n"
        f"Твой номер в очереди: {position}.\n"
        "Если кто-то откажется, место перейдёт к тебе, и бот сразу пришлёт билет."
    )


PROMOTED = "<b>Место освободилось, и оно твоё!</b>\n\nБилет ниже."
ALREADY_WAITLIST = (
    "Ты уже в листе ожидания, номер в очереди: {position}.\n\n"
    "Если место освободится, бот сразу пришлёт билет."
)
NO_TICKET = "Билета пока нет.\n\nНажми «Ближайшая встреча» внизу, чтобы записаться."

# --- отмена ---
CANCEL_CONFIRM = (
    "<b>Точно не сможешь прийти?</b>\n\n"
    "Если отменишь, твоё место уйдёт тому, кто ждёт в листе ожидания."
)
CANCEL_DONE = (
    "Регистрацию отменили.\n\n"
    "Если планы поменяются, нажми «Ближайшая встреча» и запишись снова."
)
CANCEL_KEPT = "Отлично, ждём тебя!"
CANCEL_NOTHING = "У тебя нет активной регистрации."

# --- прочие команды ---
CLUB_EMPTY = "Канал и чат скоро появятся."


def club_text(channel: str, chat: str) -> str:
    lines = []
    if channel:
        lines.append(f'Канал клуба: <a href="{escape(channel, quote=True)}">{escape(channel)}</a>')
    if chat:
        lines.append(f'Чат клуба: <a href="{escape(chat, quote=True)}">{escape(chat)}</a>')
    return "\n".join(lines) if lines else CLUB_EMPTY


# [TODO] название маркетингового агентства: пока не задано, в тексте не упоминаем по имени
AGENCY_NAME = ""


def organizers_line() -> str:
    agency = f"агентство {AGENCY_NAME}" if AGENCY_NAME else "маркетинговое агентство"
    return f"Организаторы: студенческое сообщество ШАГ, {agency} и JBD."


def about_text(policy_url: str) -> str:
    policy = (
        f'<a href="{escape(policy_url, quote=True)}">Политика обработки персональных данных</a>'
        if policy_url
        else "Политика обработки персональных данных скоро будет опубликована."
    )
    return (
        "<b>Vitalis</b> — бизнес-клуб для студентов медицинских вузов и всех, кто рядом с медициной и биотехом. "
        "Мы хотим, чтобы у российской медицины и биотеха были свои сильные компании.\n\n"
        f"{organizers_line()}\n"
        f"Бот и обработка данных: JBD ({JBD_SITE}), {OPERATOR_EMAIL}\n\n"
        f"{policy}\n"
        "Отписаться от новостей: /unsubscribe\n"
        "Удалить свои данные: /delete_me"
    )


UNSUBSCRIBED = (
    "Готово, от новостей отписали.\n\n"
    "Билет и напоминания о встречах, на которые ты записан, всё равно придут."
)
DELETE_CONFIRM = (
    "<b>Удалить все твои данные?</b>\n\n"
    "Регистрация и билет будут аннулированы. Чтобы снова записаться, анкету придётся заполнить заново."
)
DELETE_DONE = "Данные удалены."
DELETE_KEPT = "Ничего не удаляли."
