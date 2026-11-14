from datetime import datetime, timezone

from bot.db.models import Event, Registration, User
from bot.services.pdf_ticket import render_ticket_pdf


def _objs():
    user = User(id=1, full_name="Салтыкова-Щедрина Анна-Мария Евграфовна")
    event = Event(
        id=1,
        title="Встреча #1",
        speaker="со-основатель Rapid Bio",
        starts_at=datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc),
        venue_text="Совместный цифровой кампус «Школы 21» и Сеченовского университета, Москва, "
        "Зубовский бульвар, д. 37, стр. 1, м. «Парк культуры»",
    )
    reg = Registration(id=1, user_id=1, event_id=1, state="registered", ticket_no=42)
    return user, event, reg


def test_pdf_renders_with_cyrillic():
    data = render_ticket_pdf(*_objs(), secret="x" * 40)
    assert data.startswith(b"%PDF") and len(data) < 200_000
    # шрифт DejaVu встроен, значит кириллица нарисуется на любом устройстве
    assert b"DejaVu" in data
