from __future__ import annotations

from io import BytesIO
from typing import Protocol

from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader

from app.domain.errors import EvidenceParseFailed
from app.domain.policies import sha256_text


class OcrPort(Protocol):
    def recognize(self, image_bytes: bytes) -> str:
        ...


class TesseractOcr:
    def recognize(self, image_bytes: bytes) -> str:
        try:
            import pytesseract
        except ImportError as exc:
            raise EvidenceParseFailed("tesseract tidak tersedia") from exc
        with Image.open(BytesIO(image_bytes)) as image:
            prepared = ImageOps.grayscale(image)
            prepared = prepared.filter(ImageFilter.SHARPEN)
            prepared = ImageOps.autocontrast(prepared)
            text = pytesseract.image_to_string(prepared, timeout=15)
        return text.strip()


class NullOcr:
    def recognize(self, image_bytes: bytes) -> str:
        raise EvidenceParseFailed("ocr disabled")


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def extract_plain(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def excerpt_hash(text: str) -> str:
    snippet = text[:240]
    return sha256_text(snippet)
