"""Начальная схема

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("username", sa.Text()),
        sa.Column("tg_first_name", sa.Text()),
        sa.Column("full_name", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("university", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("interests", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("project_stage", sa.Text()),
        sa.Column("age_group", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.Column("newsletter_opt_in", sa.Boolean(), server_default="false", nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("text_version", sa.Text(), nullable=False),
        _ts("granted_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("type in ('pdn','newsletter')", name="consent_type"),
    )
    op.create_index("ix_consents_user_id", "consents", ["user_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue_text", sa.Text()),
        sa.Column("venue_url", sa.Text()),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("registration_limit", sa.Integer(), nullable=False),
        sa.Column("is_open", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("shared_widget_id", sa.Text()),
    )
    op.create_table(
        "registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ticket_no", sa.Integer()),
        sa.Column("source", sa.Text()),
        sa.Column("ticket_widget_msg_id", sa.BigInteger()),
        sa.Column("ticket_widget_status", sa.Text()),
        sa.Column("waitlisted_at", sa.DateTime(timezone=True)),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.Column("feedback_score", sa.SmallInteger()),
        sa.Column("reminded_24h", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reminded_2h", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("live_sent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("feedback_sent", sa.Boolean(), server_default="false", nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("user_id", "event_id", name="uq_registration_user_event"),
        sa.UniqueConstraint("event_id", "ticket_no", name="uq_registration_event_ticket"),
        sa.CheckConstraint("state in ('registered','waitlist','cancelled')", name="registration_state"),
    )
    op.create_index("ix_registrations_user_id", "registrations", ["user_id"])
    op.create_index("ix_registrations_event_id", "registrations", ["event_id"])
    op.create_table(
        "fsm_state",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text()),
        sa.Column("data", postgresql.JSONB(), server_default="{}", nullable=False),
        _ts("updated_at"),
    )
    op.create_table(
        "deletion_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        _ts("deleted_at"),
    )


def downgrade() -> None:
    for t in ("deletion_log", "fsm_state", "registrations", "events", "consents", "users"):
        op.drop_table(t)
