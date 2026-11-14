"""Первая встреча: название без номера и спикер полностью

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

from bot.db.seed_data import SPEAKER, TITLE

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HTML карточки неизменяем, поэтому сбрасываем shared-виджет: при следующем /start создастся новый
    op.execute(
        sa.text(
            "UPDATE events SET title = :t, speaker = :s, shared_widget_id = NULL "
            "WHERE id = 1 AND title = 'Встреча #1'"
        ).bindparams(t=TITLE, s=SPEAKER)
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE events SET title = 'Встреча #1', speaker = 'со-основатель Rapid Bio' WHERE id = 1")
    )
