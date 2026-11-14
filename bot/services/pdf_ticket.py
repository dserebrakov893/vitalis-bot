"""PDF-билет (план Б): A6, кириллица через DejaVu Sans, QR с подписанным номером без ПДн."""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from bot.config import JBD_SITE
from bot.db.models import Event, Registration, User
from bot.formatting import fmt_date, fmt_time, fmt_weekday
from bot.services.tickets import make_payload

FONTS = Path(__file__).resolve().parents[2] / "fonts"
INK = HexColor("#14213d")
ACCENT = HexColor("#0f766e")
MUTED = HexColor("#6b7280")


@lru_cache(maxsize=1)
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))


def _qr_image(payload: str) -> ImageReader:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def render_ticket_pdf(user: User, event: Event, reg: Registration, secret: str | None = None) -> bytes:
    _register_fonts()
    width, height = A6
    margin = 9 * mm
    text_w = width - 2 * margin
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A6)
    c.setTitle(f"Vitalis, билет №{reg.ticket_no}")
    c.setAuthor("JBD")

    # шапка: текстовый логотип (логотип Vitalis [TODO])
    c.setFillColor(INK)
    c.rect(0, height - 24 * mm, width, 24 * mm, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("DejaVu-Bold", 22)
    c.drawString(margin, height - 14 * mm, "VITALIS")
    c.setFont("DejaVu", 8)
    c.drawString(margin, height - 19.5 * mm, "бизнес-клуб · медицина и биотех")
    c.setFont("DejaVu-Bold", 12)
    c.drawRightString(width - margin, height - 14 * mm, f"№ {reg.ticket_no}")

    y = height - 33 * mm
    c.setFillColor(INK)
    c.setFont("DejaVu-Bold", 12.5)
    for line in simpleSplit(user.full_name or "", "DejaVu-Bold", 12.5, text_w)[:3]:
        c.drawString(margin, y, line)
        y -= 15.5
    y -= 2

    c.setFillColor(ACCENT)
    c.setFont("DejaVu-Bold", 10.5)
    c.drawString(margin, y, f"{fmt_date(event.starts_at)}, {fmt_weekday(event.starts_at)}, {fmt_time(event.starts_at)} МСК")
    y -= 14

    c.setFillColor(INK)
    c.setFont("DejaVu", 8)
    for line in simpleSplit(event.venue_text or "", "DejaVu", 8, text_w)[:4]:
        c.drawString(margin, y, line)
        y -= 10.5
    y -= 2
    c.setFillColor(MUTED)
    for line in simpleSplit(f"{event.title} · спикер: {event.speaker}", "DejaVu", 8, text_w)[:2]:
        c.drawString(margin, y, line)
        y -= 10.5

    # QR по центру нижней части
    qr_size = min(56 * mm, y - 22 * mm)
    qr_x = (width - qr_size) / 2
    qr_y = 15 * mm
    c.drawImage(_qr_image(make_payload(event.id, reg.ticket_no, secret)), qr_x, qr_y, qr_size, qr_size)
    c.setFont("DejaVu", 6.5)
    c.setFillColor(MUTED)
    c.drawCentredString(width / 2, qr_y - 4 * mm, "Покажи QR-код на входе")

    c.setFont("DejaVu", 6)
    c.drawCentredString(width / 2, 5 * mm, f"Билет сделан JBD · {JBD_SITE}")
    c.showPage()
    c.save()
    return buf.getvalue()
