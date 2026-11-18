"""Чекин на входе: проверяющие, приглашения, кто отметил

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column("registrations", sa.Column("checked_in_by", sa.BigInteger()))
    op.create_table(
        "checkers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by", sa.BigInteger()),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "event_id", name="uq_checker_user_event"),
    )
    op.create_index("ix_checkers_user_id", "checkers", ["user_id"])
    op.create_index("ix_checkers_event_id", "checkers", ["event_id"])
    op.create_table(
        "checker_invites",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_by", sa.BigInteger()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("checker_invites")
    op.drop_table("checkers")
    op.drop_column("registrations", "checked_in_by")
