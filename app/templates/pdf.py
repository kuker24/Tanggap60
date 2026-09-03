from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

WARM = HexColor("#271503")
EMBER = HexColor("#BE3F00")
MUTED = HexColor("#5E5750")
LINEN = HexColor("#F3F2EE")
HAIR = HexColor("#E5E7EB")
RED = HexColor("#991B1B")
RED_SOFT = HexColor("#FFF1F2")
AMBER = HexColor("#B45309")
AMBER_SOFT = HexColor("#FFF7ED")

BANNER_DRAF = "DRAF PENGGUNA — BUKAN DOKUMEN RESMI"
BANNER_STATUS = "STATUS RESMI: NOT_VERIFIED"
BANNER_GAP = "BELUM LENGKAP — PERLU TINDAKAN"
_SKIP_PREFIXES = (
    "Snapshot ",
    "Kasus case-",
    "Action Plan -",
    "Emergency Action Plan",
    "Evidence Pack -",
    "Verification Brief -",
)


def render_lines(title: str, lines: Iterable[str], generated_at: str, snapshot_hash: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    margin = 18 * mm
    max_width = width - (2 * margin)
    footer_top = 16 * mm
    raw = [str(item) for item in lines]
    banners = [BANNER_DRAF, BANNER_STATUS]
    if any(item.strip() == BANNER_GAP or BANNER_GAP in item for item in raw):
        banners.insert(1, BANNER_GAP)
    body: list[str] = []
    for item in raw:
        text = item.strip()
        if not text:
            body.append("")
            continue
        if text in {BANNER_DRAF, BANNER_STATUS, BANNER_GAP}:
            continue
        if any(text.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue
        body.append(item.rstrip())

    def header() -> None:
        pdf.setFillColor(WARM)
        pdf.rect(0, height - 14 * mm, width, 14 * mm, fill=1, stroke=0)
        pdf.setFillColor(EMBER)
        pdf.rect(0, height - 14 * mm, 3.2 * mm, 14 * mm, fill=1, stroke=0)
        pdf.setFillColor(white)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(margin, height - 9 * mm, "SatuAman")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(margin + 26 * mm, height - 9 * mm, "Tanggap60")
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawRightString(width - margin, height - 9 * mm, "DRAF")

    def footer() -> None:
        pdf.setFillColor(LINEN)
        pdf.rect(0, 0, width, 14 * mm, fill=1, stroke=0)
        pdf.setStrokeColor(HAIR)
        pdf.setLineWidth(0.4)
        pdf.line(0, 14 * mm, width, 14 * mm)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 7.5)
        pdf.drawString(margin, 6 * mm, "Bukan dokumen resmi. Tidak dikirim ke bank atau polisi.")
        stamp = f"{str(generated_at)[:10]}  ·  snapshot {snapshot_hash[:16]}"
        pdf.drawRightString(width - margin, 6 * mm, stamp)

    def wrap(text: str, font: str, size: float, limit: float) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        out: list[str] = []
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if pdf.stringWidth(trial, font, size) <= limit:
                current = trial
            else:
                out.append(current)
                current = word
        out.append(current)
        return out

    y = height - 24 * mm

    def new_page() -> None:
        nonlocal y
        footer()
        pdf.showPage()
        header()
        y = height - 22 * mm

    def need(space: float) -> None:
        nonlocal y
        if y - space < footer_top + 4 * mm:
            new_page()

    def paint(text: str, font: str, size: float, color: HexColor, leading: float, indent: float = 0) -> None:
        nonlocal y
        limit = max_width - indent
        for piece in wrap(text, font, size, limit):
            need(leading)
            pdf.setFillColor(color)
            pdf.setFont(font, size)
            pdf.drawString(margin + indent, y, piece)
            y -= leading

    header()
    pdf.setFillColor(WARM)
    pdf.setFont("Times-Bold", 20)
    for piece in wrap(title, "Times-Bold", 20, max_width):
        pdf.drawString(margin, y, piece)
        y -= 8 * mm
    y -= 2 * mm

    box_color = RED_SOFT if BANNER_GAP in banners else AMBER_SOFT
    ink = RED if BANNER_GAP in banners else AMBER
    banner_lines: list[str] = []
    for banner in banners:
        banner_lines.extend(wrap(banner, "Helvetica-Bold", 8.5, max_width - 8 * mm))
    box_h = 7 * mm + len(banner_lines) * 4.2 * mm
    need(box_h + 4 * mm)
    pdf.setFillColor(box_color)
    pdf.roundRect(margin - 2 * mm, y - box_h + 4 * mm, max_width + 4 * mm, box_h, 3 * mm, fill=1, stroke=0)
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 8.5)
    text_y = y - 1 * mm
    for piece in banner_lines:
        pdf.drawString(margin + 2 * mm, text_y, piece)
        text_y -= 4.2 * mm
    y -= box_h + 4 * mm

    for item in body:
        text = item.strip()
        if not text:
            y -= 3 * mm
            continue
        if text.startswith("## "):
            y -= 2 * mm
            paint(text[3:], "Helvetica-Bold", 11, WARM, 6 * mm)
            pdf.setStrokeColor(HAIR)
            pdf.setLineWidth(0.5)
            pdf.line(margin, y + 3.5 * mm, margin + max_width, y + 3.5 * mm)
            continue
        if text.startswith("- "):
            paint("•  " + text[2:], "Times-Roman", 10.5, WARM, 5.4 * mm, 3 * mm)
            continue
        paint(text, "Times-Roman", 10.5, WARM, 5.4 * mm)

    footer()
    pdf.save()
    return buffer.getvalue()
