"""ORM-модели. Время везде timestamptz (UTC), показываем в Europe/Moscow."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

USER_STATUSES = ("student", "resident", "doctor", "other")
INTERESTS = ("private_practice", "medtech", "pharma_biotech", "undecided")
PROJECT_STAGES = ("running", "idea", "none")
AGE_GROUPS = ("adult", "17_with_parent")
REG_STATES = ("registered", "waitlist", "cancelled")


class Base(DeclarativeBase):
    pass


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, **kw)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(Text)
    tg_first_name: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    university: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    interests: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    project_stage: Mapped[str | None] = mapped_column(Text)
    age_group: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    newsletter_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts(onupdate=func.now())

    registrations: Mapped[list["Registration"]] = relationship(back_populates="user", passive_deletes=True)


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(Text, CheckConstraint("type in ('pdn','newsletter')", name="consent_type"))
    text_version: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[datetime] = _ts()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    speaker: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # пусто = начало + 3 часа
    venue_text: Mapped[str | None] = mapped_column(Text)
    venue_url: Mapped[str | None] = mapped_column(Text)
    seats: Mapped[int] = mapped_column(Integer)
    registration_limit: Mapped[int] = mapped_column(Integer)
    is_open: Mapped[bool] = mapped_column(Boolean, server_default="true")
    shared_widget_id: Mapped[str | None] = mapped_column(Text)


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_registration_user_event"),
        UniqueConstraint("event_id", "ticket_no", name="uq_registration_event_ticket"),
        CheckConstraint("state in ('registered','waitlist','cancelled')", name="registration_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(Text)
    ticket_no: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(Text)
    ticket_widget_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    ticket_widget_status: Mapped[str | None] = mapped_column(Text)  # последний записанный статус виджета
    waitlisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_score: Mapped[int | None] = mapped_column(SmallInteger)
    reminded_24h: Mapped[bool] = mapped_column(Boolean, server_default="false")
    reminded_2h: Mapped[bool] = mapped_column(Boolean, server_default="false")
    live_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    feedback_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts(onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="registrations")
    event: Mapped[Event] = relationship()


class Feedback(Base):
    """Отзывы: о встрече (одна запись на человека и встречу) и о клубе в целом (сколько угодно)."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("kind in ('event','club')", name="feedback_kind"),
        CheckConstraint("score is null or score between 1 and 5", name="feedback_score"),
        Index("uq_feedback_event_user", "event_id", "user_id", unique=True,
              postgresql_where=text("kind = 'event'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("events.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(SmallInteger)
    attended: Mapped[bool | None] = mapped_column(Boolean)  # False: «Не смог прийти»
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    category: Mapped[str | None] = mapped_column(Text)  # для отзывов о клубе
    comment: Mapped[str | None] = mapped_column(Text)
    next_topic: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts(onupdate=func.now())


class FsmState(Base):
    """Хранилище FSM aiogram. В той же БД, что и остальные ПДн: анкета не уходит в сторонний Redis."""

    __tablename__ = "fsm_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = _ts(onupdate=func.now())


class DeletionLog(Base):
    """Журнал удалений по /delete_me: только Telegram ID и дата."""

    __tablename__ = "deletion_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    deleted_at: Mapped[datetime] = _ts()
