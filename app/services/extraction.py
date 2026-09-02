from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader

from app.domain.errors import EvidenceParseFailed
from app.domain.policies import sha256_bytes, sha256_text


@dataclass
class PageText:
    page: int
    text: str
    boxes: list[tuple[str, int, int, int, int]] = field(default_factory=list)


class OcrPort(Protocol):
    def recognize(self, image_bytes: bytes) -> str:
        ...


class TesseractOcr:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, list[tuple[str, int, int, int, int]]]] = {}

    def recognize(self, image_bytes: bytes) -> str:
        return self._run(image_bytes)[0]

    def recognize_boxes(self, image_bytes: bytes) -> list[tuple[str, int, int, int, int]]:
        return self._run(image_bytes)[1]

    def _run(self, image_bytes: bytes) -> tuple[str, list[tuple[str, int, int, int, int]]]:
        digest = sha256_bytes(image_bytes)
        cached = self._cache.get(digest)
        if cached is not None:
            return cached
        try:
            import pytesseract
        except ImportError as exc:
            raise EvidenceParseFailed("tesseract tidak tersedia") from exc
        with Image.open(BytesIO(image_bytes)) as image:
            prepared = ImageOps.grayscale(image)
            prepared = prepared.filter(ImageFilter.SHARPEN)
            prepared = ImageOps.autocontrast(prepared)
            payload = pytesseract.image_to_data(prepared, timeout=15, output_type=pytesseract.Output.DICT)
        boxes: list[tuple[str, int, int, int, int]] = []
        words: list[str] = []
        n = len(payload.get("text") or [])
        for i in range(n):
            word = str(payload["text"][i]).strip()
            if not word:
                continue
            words.append(word)
            boxes.append(
                (
                    word,
                    int(payload["left"][i]),
                    int(payload["top"][i]),
                    int(payload["width"][i]),
                    int(payload["height"][i]),
                )
            )
        result = (" ".join(words).strip(), boxes)
        self._cache[digest] = result
        return result


class NullOcr:
    def recognize(self, image_bytes: bytes) -> str:
        raise EvidenceParseFailed("ocr disabled")


def extract_pdf_pages(data: bytes) -> list[PageText]:
    reader = PdfReader(BytesIO(data))
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append(PageText(page=index, text=(page.extract_text() or "").strip()))
    return pages


def extract_pdf_text(data: bytes) -> str:
    return "\n".join(page.text for page in extract_pdf_pages(data)).strip()


def extract_plain(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def excerpt_hash(text: str) -> str:
    snippet = text[:240]
    return sha256_text(snippet)


def encode_pages(pages: list[PageText]) -> bytes:
    payload = {
        "pages": [
            {
                "page": page.page,
                "text": page.text,
                "boxes": [[w, x, y, ww, hh] for w, x, y, ww, hh in page.boxes],
            }
            for page in pages
        ]
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def decode_pages(data: bytes) -> list[PageText]:
    raw = data.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
        pages = payload.get("pages")
        if isinstance(pages, list) and pages:
            result: list[PageText] = []
            for item in pages:
                boxes = []
                for box in item.get("boxes") or []:
                    if len(box) == 5:
                        boxes.append((str(box[0]), int(box[1]), int(box[2]), int(box[3]), int(box[4])))
                result.append(PageText(page=int(item.get("page") or 1), text=str(item.get("text") or ""), boxes=boxes))
            return result
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    return [PageText(page=1, text=raw)]


def locator_for(raw: str, page: int, start: int, end: int, boxes: list[tuple[str, int, int, int, int]]) -> str:
    box = bbox_for_value(raw, boxes)
    if box:
        return f"p{page}:{box}"
    return f"p{page}:o{start}-{end}"


def bbox_for_value(raw: str, boxes: list[tuple[str, int, int, int, int]]) -> str | None:
    if not boxes or not raw:
        return None
    needle = "".join(raw.split())
    joined = ""
    index_at: list[int] = []
    for i, (word, *_rest) in enumerate(boxes):
        for _ch in word:
            index_at.append(i)
        joined += word
    compact = "".join(joined.split())
    pos = compact.find(needle)
    if pos < 0:
        return None
    first = index_at[min(pos, len(index_at) - 1)]
    last = index_at[min(pos + len(needle) - 1, len(index_at) - 1)]
    xs: list[int] = []
    ys: list[int] = []
    x2: list[int] = []
    y2: list[int] = []
    for word, x, y, w, h in boxes[first : last + 1]:
        xs.append(x)
        ys.append(y)
        x2.append(x + w)
        y2.append(y + h)
    return f"{min(xs)},{min(ys)},{max(x2)},{max(y2)}"
