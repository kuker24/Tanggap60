from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def render_lines(title: str, lines: Iterable[str], generated_at: str, snapshot_hash: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    y = height - 20 * mm
    pdf.setFont("Times-Bold", 16)
    pdf.drawString(20 * mm, y, title)
    y -= 10 * mm
    pdf.setFont("Times-Roman", 11)
    for line in lines:
        for wrapped in _wrap(line, 95):
            if y < 20 * mm:
                pdf.showPage()
                pdf.setFont("Times-Roman", 11)
                y = height - 20 * mm
            pdf.drawString(20 * mm, y, wrapped)
            y -= 6 * mm
    y -= 8 * mm
    pdf.setFont("Times-Roman", 9)
    pdf.drawString(20 * mm, 15 * mm, f"{generated_at} | snapshot {snapshot_hash[:16]}")
    pdf.save()
    return buffer.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if len(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
