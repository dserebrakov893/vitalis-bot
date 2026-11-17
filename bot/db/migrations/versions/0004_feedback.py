"""Время окончания встречи и таблица отзывов

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column("events", sa.Column("ends_at", sa.DateTime(timezone=True)))
    # первая встреча: 17:00–20:00 МСК
    op.execute("UPDATE events SET ends_at = '2026-11-19 17:00+00' WHERE id = 1 AND ends_at IS NULL")
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("score", sa.SmallInteger()),
        sa.Column("attended", sa.Boolean()),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("category", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("next_topic", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("kind in ('event','club')", name="feedback_kind"),
        sa.CheckConstraint("score is null or score between 1 and 5", name="feedback_score"),
    )
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_index("ix_feedback_event_id", "feedback", ["event_id"])
    # один отзыв о встрече на человека; отзывов о клубе может быть сколько угодно
    op.create_index(
        "uq_feedback_event_user", "feedback", ["event_id", "user_id"], unique=True,
        postgresql_where=sa.text("kind = 'event'"),
    )


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_column("events", "ends_at")
