"""PDF ticket rendering (fpdf2 + segno, both pure Python)."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import segno
from fpdf import FPDF


def _qr_png(qr_token: str) -> Path:
    qr = segno.make(qr_token, error="h")
    descriptor, raw_path = tempfile.mkstemp(suffix=".png")
    os.close(descriptor)
    path = Path(raw_path)
    qr.save(str(path), scale=8, border=2)
    return path


def render_ticket_pdf(
    *,
    ticket_id: str,
    qr_token: str,
    owner_email: str,
    order_id: str,
    event_title: str = "TicketForge",
) -> bytes:
    qr_path = _qr_png(qr_token)
    try:
        pdf = FPDF(format="A5", unit="mm")
        pdf.add_page()
        pdf.set_fill_color(17, 24, 39)
        pdf.rect(0, 0, 148, 30, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_xy(12, 9)
        pdf.cell(0, 10, "TicketForge")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(12, 19)
        pdf.cell(0, 6, "ADMIT ONE")

        pdf.set_text_color(17, 24, 39)
        pdf.set_xy(12, 40)
        pdf.set_font("Helvetica", "B", 14)
        pdf.multi_cell(80, 7, event_title[:60])

        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(12, 58)
        pdf.multi_cell(80, 6, f"Order\n{order_id}")
        pdf.set_xy(12, 76)
        pdf.multi_cell(80, 6, f"Ticket\n{ticket_id}")
        pdf.set_xy(12, 94)
        pdf.multi_cell(80, 6, f"Holder\n{owner_email}")

        pdf.image(str(qr_path), x=104, y=44, w=36)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_xy(100, 84)
        pdf.multi_cell(40, 4, "Scan at the door. Each ticket is valid for one entry.")

        output = pdf.output()
        return bytes(output)
    finally:
        qr_path.unlink(missing_ok=True)


def render_ticket_pdf_bytes(**kwargs: str) -> io.BytesIO:
    return io.BytesIO(render_ticket_pdf(**kwargs))
