"""PDF ticket rendering (fpdf2 + segno, both pure Python, no system deps).

The layout mirrors the site: a riso-blue band with the wordmark, the event in
large type, the details as label/value pairs, and a big QR code below a
perforation line so it scans reliably from a phone screen or a printout.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import segno
from fpdf import FPDF

INK = (21, 23, 46)
MUTED = (91, 95, 120)
BLUE = (43, 79, 216)
PINK = (255, 79, 163)
PAPER = (252, 252, 250)

# fpdf2's built-in Helvetica covers Latin-1 only. Map common typographic
# characters first, then replace anything else rather than crash on a title.
_TYPOGRAPHY = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}
)


def _latin1(value: str) -> str:
    return value.translate(_TYPOGRAPHY).encode("latin-1", "replace").decode("latin-1")


def _qr_png(qr_token: str) -> Path:
    qr = segno.make(qr_token, error="h")
    descriptor, raw_path = tempfile.mkstemp(suffix=".png")
    os.close(descriptor)
    path = Path(raw_path)
    qr.save(str(path), scale=10, border=2)
    return path


def render_ticket_pdf(
    *,
    ticket_id: str,
    qr_token: str,
    owner_email: str,
    order_id: str,
    event_title: str = "TicketForge",
    when: str = "",
    venue: str = "",
    admission: str = "",
) -> bytes:
    qr_path = _qr_png(qr_token)
    try:
        pdf = FPDF(format=(148, 210), unit="mm")  # ISO A5 portrait
        pdf.set_auto_page_break(False)
        pdf.set_title(_latin1(f"Ticket: {event_title}"))
        pdf.set_author("TicketForge")
        pdf.add_page()
        pdf.set_fill_color(*PAPER)
        pdf.rect(0, 0, 148, 210, style="F")

        # band
        pdf.set_fill_color(*BLUE)
        pdf.rect(0, 0, 148, 26, style="F")
        pdf.set_fill_color(*PINK)
        pdf.ellipse(118, 4, 18, 18, style="F")
        pdf.set_text_color(*PAPER)
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_xy(12, 8)
        pdf.cell(0, 10, "TicketForge")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_xy(84, 10)
        pdf.cell(30, 6, "Admit one", align="R")

        # event
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_xy(12, 34)
        pdf.multi_cell(124, 9, _latin1(event_title[:80].upper()), align="L")

        y = pdf.get_y() + 4
        for label, value in (
            ("When", when),
            ("Where", venue),
            ("Admission", admission),
            ("Ticket holder", owner_email),
        ):
            if not value:
                continue
            pdf.set_xy(12, y)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*MUTED)
            pdf.cell(124, 4, label)
            pdf.set_xy(12, y + 4)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*INK)
            pdf.multi_cell(124, 5.5, _latin1(value), align="L")
            y = pdf.get_y() + 3

        # perforation
        perforation_y = max(y + 4, 118)
        pdf.set_draw_color(*MUTED)
        pdf.set_dash_pattern(dash=1.2, gap=1.2)
        pdf.line(8, perforation_y, 140, perforation_y)
        pdf.set_dash_pattern()

        # QR
        size = min(62, 210 - perforation_y - 26)
        pdf.image(str(qr_path), x=(148 - size) / 2, y=perforation_y + 6, w=size)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.set_xy(12, perforation_y + size + 8)
        pdf.multi_cell(
            124,
            4,
            _latin1(
                f"Show this code at the door. It is valid for one entry.\n"
                f"Ticket {ticket_id[:8].upper()}  Order {order_id[:8].upper()}"
            ),
            align="C",
        )
        return bytes(pdf.output())
    finally:
        qr_path.unlink(missing_ok=True)


def render_ticket_pdf_bytes(**kwargs: str) -> io.BytesIO:
    return io.BytesIO(render_ticket_pdf(**kwargs))
