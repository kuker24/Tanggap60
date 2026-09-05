from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

CHAT = "Kirim dulu Rp2.500.000 ke rekening ini ya biar pesanan diproses"
TRANSFER = "Transfer Berhasil Rp2.750.000 Ke: DEMO-DEST-01 23 September 2026 08:42 WIB Dari: DEMO-VICTIM-MASKED"


def font(size: int) -> ImageFont.ImageFont:
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def png_bytes(text: str, *, width: int = 900, height: int = 240, size: int = 24) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, height // 3), text, fill="black", font=font(size))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def image_only_pdf(png: bytes) -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.drawImage(ImageReader(BytesIO(png)), 72, 600, width=400, height=120)
    pdf.save()
    return buf.getvalue()


def mixed_text_and_image_pdf(png: bytes, text_page: str) -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setFont("Helvetica", 16)
    pdf.drawString(72, 720, text_page)
    pdf.showPage()
    pdf.drawImage(ImageReader(BytesIO(png)), 72, 600, width=400, height=120)
    pdf.save()
    return buf.getvalue()


def invoice_pdf() -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setFont("Helvetica", 16)
    pdf.drawString(72, 720, "INVOICE DEMO")
    pdf.drawString(72, 690, "Tanggap60 fixture page 1")
    pdf.showPage()
    pdf.setFont("Helvetica", 16)
    pdf.drawString(72, 720, "Tagihan Rp2.500.000")
    pdf.drawString(72, 690, "Rekening DEMO-DEST-01")
    pdf.drawString(72, 660, "23 September 2026")
    pdf.save()
    return buf.getvalue()
