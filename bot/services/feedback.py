"""Отзывы: о встрече (оценка, пункты, комментарий, пожелание) и о клубе в целом."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import Event, Feedback, Registration, User
from bot.formatting import event_end
from bot.services.registration import utcnow

IMPROVE = [("topic", "Тема"), ("speaker", "Спикер"), ("venue", "Площадка"), ("time", "Время"),
           ("org", "Организация"), ("other", "Другое")]
LIKED = [("topic", "Тема"), ("speaker", "Спикер"), ("people", "Люди и знакомства"), ("format", "Формат"),
         ("org", "Организация")]
CLUB_CATEGORIES = [("idea", "Идея для клуба"), ("wish", "Пожелание"), ("bot", "Проблема с ботом"), ("other", "Другое")]
TAG_LABELS = {**dict(IMPROVE), **dict(LIKED)}
CATEGORY_LABELS = dict(CLUB_CATEGORIES)

SURVEY_AFTER_END = timedelta(minutes=30)
WINDOW = timedelta(days=7)  # сколько после опроса принимаем отзывы о встрече
MAX_TEXT = 1000


def survey_at(event: Event) -> datetime:
    return event_end(event) + SURVEY_AFTER_END


def is_open_for_feedback(event: Event, now: datetime | None = None) -> bool:
    """Отзыв о встрече можно оставить с её начала и до недели после опроса."""
    now = now or utcnow()
    return event.starts_at <= now < survey_at(event) + WINDOW


def tags_for(score: int | None) -> list[tuple[str, str]]:
    return IMPROVE if score is not None and score <= 3 else LIKED


def event_button_label(event: Event) -> str:
    title = event.title or ""
    if title.lower().startswith("встреча "):
        return "О встрече " + title[len("встреча "):]
    return f"О встрече «{title}»"


async def event_for_feedback(sm: async_sessionmaker, user_id: int, now: datetime | None = None) -> Event | None:
    """Последняя встреча, на которую человек был записан и о которой ещё можно оставить отзыв."""
    now = now or utcnow()
    async with sm() as s:
        events = (
            await s.execute(
                select(Event)
                .join(Registration, Registration.event_id == Event.id)
                .where(Registration.user_id == user_id, Registration.state == "registered", Event.starts_at <= now)
                .order_by(Event.starts_at.desc())
            )
        ).scalars().all()
    for ev in events:
        if is_open_for_feedback(ev, now):
            return ev
    return None


async def get_event_feedback(sm: async_sessionmaker, user_id: int, event_id: int) -> Feedback | None:
    async with sm() as s:
        return (
            await s.execute(
                select(Feedback).where(
                    Feedback.kind == "event", Feedback.user_id == user_id, Feedback.event_id == event_id
                )
            )
        ).scalar_one_or_none()


async def save_event_feedback(sm: async_sessionmaker, user_id: int, event_id: int, **fields) -> None:
    """Создаёт или дополняет отзыв о встрече (одна запись на человека и встречу)."""
    values = {"user_id": user_id, "event_id": event_id, "kind": "event", **fields}
    async with sm() as s, s.begin():
        stmt = insert(Feedback).values(**values)
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=[Feedback.event_id, Feedback.user_id],
                index_where=text("kind = 'event'"),
                set_={**fields, "updated_at": text("now()")},
            )
        )
        if "score" in fields:
            # дублируем оценку в регистрацию: её показывает билет-виджет
            await s.execute(
                update(Registration)
                .where(Registration.user_id == user_id, Registration.event_id == event_id)
                .values(feedback_score=fields["score"])
            )


async def add_club_feedback(sm: async_sessionmaker, user_id: int, category: str, comment: str) -> None:
    async with sm() as s, s.begin():
        if await s.get(User, user_id) is None:
            s.add(User(id=user_id, interests=[]))
            await s.flush()
        s.add(Feedback(user_id=user_id, kind="club", category=category, comment=comment[:MAX_TEXT], tags=[]))
