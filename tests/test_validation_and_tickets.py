import pytest
from sqlalchemy import func, select

from bot.db.models import Consent, DeletionLog, FsmState, Registration, User
from bot.services import registration as reg
from bot.services import tickets
from bot.services.validation import (
    is_own_contact,
    normalize_phone,
    parse_source,
    validate_email,
    validate_full_name,
)
from tests.factories import add_users, profile
from tests.test_registration import BEFORE

SECRET = "s" * 40


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Иванов Иван Иванович", "Иванов Иван Иванович"),
        ("  Петрова   Анна ", "Петрова Анна"),
        ("Салтыков-Щедрин Михаил Евграфович", "Салтыков-Щедрин Михаил Евграфович"),
        ("Ёлкина Алёна", "Ёлкина Алёна"),
        ("John Smith", "John Smith"),
    ],
)
def test_full_name_ok(raw, expected):
    assert validate_full_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["Иван", "Иванов Иван Иванович Младший", "Иванов 1ван", "Иванов Иван!", "А Б", "Иванов -Иван",
     "Иванов Иван_", "", None, "Ив " + "а" * 100],
)
def test_full_name_rejected(raw):
    assert validate_full_name(raw) is None


@pytest.mark.parametrize(
    "raw, expected",
    [("Ivan@Example.COM", "ivan@example.com"), (" a.b+c@mail.ru ", "a.b+c@mail.ru")],
)
def test_email_ok(raw, expected):
    assert validate_email(raw) == expected


@pytest.mark.parametrize("raw", ["ivan", "ivan@", "@mail.ru", "ivan@mail", "iv an@mail.ru", "a..b@mail.ru", "ивaн@почта.рф", ""])
def test_email_rejected(raw):
    assert validate_email(raw) is None


def test_foreign_contact_rejected():
    assert is_own_contact(100, 100)
    assert not is_own_contact(200, 100)
    assert not is_own_contact(None, 100)  # контакт из записной книжки без user_id


def test_phone_and_source():
    assert normalize_phone("+7 (999) 000-00-00") == "+79990000000"
    assert parse_source("insta_story-1") == "insta_story-1"
    assert parse_source("a" * 65) is None
    assert parse_source("bad code") is None
    assert parse_source("кириллица") is None


def test_ticket_signature():
    payload = tickets.make_payload(1, 42, SECRET)
    assert payload.startswith("VTL1.1.42.") and len(payload.split(".")[3]) == 10
    assert tickets.parse_payload(payload, SECRET) == (1, 42)
    sig = payload.split(".")[3]
    forged_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert tickets.parse_payload(f"VTL1.1.42.{forged_sig}", SECRET) is None
    assert tickets.parse_payload(f"VTL1.1.43.{sig}", SECRET) is None  # подпись от другого номера
    assert tickets.parse_payload(payload, "other" * 10) is None
    assert tickets.parse_payload("garbage", SECRET) is None


async def test_verify_ticket(sessionmaker):
    await add_users(sessionmaker, [1, 2])
    r1 = await reg.register(sessionmaker, 1, 1, None, BEFORE)
    await reg.register(sessionmaker, 2, 1, None, BEFORE)
    good = tickets.make_payload(1, r1.registration.ticket_no, SECRET)
    async with sessionmaker() as s:
        found = await tickets.verify_ticket(s, good, SECRET)
        assert found is not None and found.user_id == 1
        assert await tickets.verify_ticket(s, good[:-1] + ("A" if good[-1] != "A" else "B"), SECRET) is None
        assert await tickets.verify_ticket(s, tickets.make_payload(1, 99, SECRET), SECRET) is None
    await reg.cancel(sessionmaker, 1, 1, BEFORE)  # отменённый билет недействителен
    async with sessionmaker() as s:
        assert await tickets.verify_ticket(s, good, SECRET) is None


async def test_delete_me_removes_all_personal_data(sessionmaker):
    await add_users(sessionmaker, range(2, 77))
    await reg.complete_registration(sessionmaker, 1, 1, profile(newsletter=True), "test", BEFORE)
    for uid in range(2, 77):  # 2..75 зарегистрированы, 76 в листе ожидания
        await reg.register(sessionmaker, uid, 1, None, BEFORE)
    async with sessionmaker() as s, s.begin():
        s.add(FsmState(key="999:1:1:default", state="Form:email", data={"full_name": "Иванов Иван"}))
        s.add(FsmState(key="999:11:11:default", state=None, data={}))

    promoted = await reg.delete_user(sessionmaker, 1, BEFORE)
    assert [p.user_id for p in promoted] == [76]

    async with sessionmaker() as s:
        assert await s.get(User, 1) is None
        for model in (Registration, Consent):
            assert (await s.execute(select(func.count()).where(model.user_id == 1))).scalar_one() == 0
        keys = (await s.execute(select(FsmState.key))).scalars().all()
        assert keys == ["999:11:11:default"]
        log = (await s.execute(select(DeletionLog))).scalars().all()
    assert [(row.telegram_id, row.deleted_at is not None) for row in log] == [(1, True)]
    # в журнале нет других полей, кроме id, telegram_id и даты
    assert set(DeletionLog.__table__.columns.keys()) == {"id", "telegram_id", "deleted_at"}
