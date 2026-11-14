"""Сид: первая встреча клуба (19.11.2026, 17:00 МСК)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""
from datetime import datetime, timezone
from urllib.parse import quote

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TITLE = "Встреча #1"
VENUE_TEXT = (
    "Совместный цифровой кампус «Школы 21» и Сеченовского университета, "
    "Москва, Зубовский бульвар, д. 37, стр. 1, м. «Парк культуры»"
)
VENUE_URL = "https://yandex.ru/maps/?text=" + quote("Москва, Зубовский бульвар, 37с1")


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
                "speaker": "со-основатель Rapid Bio",
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
    op.execute(sa.text("DELETE FROM events WHERE title = :t").bindparams(t=TITLE))
