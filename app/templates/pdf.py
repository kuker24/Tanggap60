from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

WARM = HexColor("#0f0f10")
CREAM = HexColor("#ffffff")
EMBER = HexColor("#0088ff")
MUTED = HexColor("#888b91")
LINEN = HexColor("#ffffff")
HAIR = HexColor("#333333")
BARK = HexColor("#1c1d1f")
RED = HexColor("#e6714f")
RED_SOFT = HexColor("#f4d4cc")
AMBER = HexColor("#1c1d1f")
AMBER_SOFT = HexColor("#e8e8ea")

BANNER_DRAF = "DRAF PENGGUNA — BUKAN DOKUMEN RESMI"
BANNER_STATUS = "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi."
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
        pdf.rect(0, height - 14 * mm, 2.2 * mm, 14 * mm, fill=1, stroke=0)
        pdf.setFillColor(CREAM)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(margin, height - 9 * mm, "SatuAman")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(margin + 26 * mm, height - 9 * mm, "Tanggap60")
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawRightString(width - margin, height - 9 * mm, "DRAF")

    def footer() -> None:
        pdf.setFillColor(BARK)
        pdf.rect(0, 0, width, 14 * mm, fill=1, stroke=0)
        pdf.setStrokeColor(HAIR)
        pdf.setLineWidth(0.4)
        pdf.line(0, 14 * mm, width, 14 * mm)
        pdf.setFillColor(CREAM)
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
    pdf.setFont("Helvetica-Bold", 18)
    for piece in wrap(title, "Helvetica-Bold", 18, max_width):
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
            pieces = wrap(text[2:], "Times-Roman", 10.5, max_width - 7 * mm)
            for index, piece in enumerate(pieces):
                need(5.4 * mm)
                if index == 0:
                    pdf.setFillColor(EMBER)
                    pdf.circle(margin + 1.5 * mm, y + 1.1 * mm, 1.05 * mm, fill=1, stroke=0)
                pdf.setFillColor(WARM)
                pdf.setFont("Times-Roman", 10.5)
                pdf.drawString(margin + 6 * mm, y, piece)
                y -= 5.4 * mm
            continue
        paint(text, "Times-Roman", 10.5, WARM, 5.4 * mm)

    footer()
    pdf.save()
    return buffer.getvalue()
