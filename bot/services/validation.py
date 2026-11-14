"""Проверка ввода в анкете и start-параметра."""
from __future__ import annotations

import re

_WORD = re.compile(r"^[^\W\d_]+(?:-[^\W\d_]+)*$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_SOURCE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_full_name(text: str | None) -> str | None:
    """2–3 слова, только буквы, дефис и пробел, длина 5–100. Возвращает нормализованное ФИО."""
    if not text:
        return None
    name = " ".join(text.split())
    if not 5 <= len(name) <= 100:
        return None
    words = name.split(" ")
    if not 2 <= len(words) <= 3:
        return None
    if not all(_WORD.fullmatch(w) for w in words):
        return None
    return name


def validate_email(text: str | None) -> str | None:
    if not text:
        return None
    email = text.strip().lower()
    if len(email) > 254 or ".." in email or not _EMAIL.fullmatch(email):
        return None
    return email


def is_own_contact(contact_user_id: int | None, from_user_id: int) -> bool:
    """Телефон принимаем только из своего контакта (кнопка request_contact)."""
    return contact_user_id is not None and contact_user_id == from_user_id


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return "+" + digits if digits else ""


def parse_source(arg: str | None) -> str | None:
    """start-параметр: [A-Za-z0-9_-]{1,64}, всё остальное игнорируем."""
    if not arg:
        return None
    arg = arg.strip()
    return arg if _SOURCE.fullmatch(arg) else None


def validate_university(text: str | None) -> str | None:
    if not text:
        return None
    value = " ".join(text.split())
    return value if 2 <= len(value) <= 100 else None
