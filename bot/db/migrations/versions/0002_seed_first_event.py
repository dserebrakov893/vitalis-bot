"""Сид: первая встреча клуба (19.11.2026, 17:00 МСК)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from bot.db.seed_data import SPEAKER, TITLE, VENUE_TEXT, VENUE_URL

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    events = sa.table(
        "events",
        sa.column("title", sa.Text),
        sa.column("speaker", sa.Text),
        sa.column("starts_at", sa.DateTime(timezone=True)),
        sa.column("venue_text", sa.Text),
        sa.column("venue_url", sa.Text),
        sa.column("seats", sa.Integer),
        sa.column("registration_limit", sa.Integer),
        sa.column("is_open", sa.Boolean),
    )
    op.bulk_insert(
        events,
        [
            {
                "title": TITLE,
                "speaker": SPEAKER,
                # 17:00 МСК = 14:00 UTC
                "starts_at": datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc),
                "venue_text": VENUE_TEXT,
                "venue_url": VENUE_URL,
                "seats": 60,
                "registration_limit": 75,
                "is_open": True,
            }
        ],
    )

def downgrade() -> None:
    op.execute(sa.text("DELETE FROM events WHERE id = 1"))
