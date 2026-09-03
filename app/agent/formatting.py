"""Formatting, parsing, dan redaksi untuk Rescue Agent.

Semua helper di sini deterministik dan tanpa I/O.
"""

from __future__ import annotations

import html
import re

# Pola rahasia yang tidak boleh disimpan/digaungkan agent.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4,6}\b"),  # kandidat OTP/kode angka
    re.compile(r"(?i)\b(otp|captcha|kata sandi|password|sandi|pin atm|kode verifikasi)\b"),
)


def escape(text: object) -> str:
    """Escape untuk JSON/HTML — output model tidak pernah mentah."""
    return html.escape(str(text), quote=False)


def mask_account(raw: object) -> str:
    """Samarkan nomor rekening: hanya 4 digit terakhir yang tampil.

    Label tanpa digit (nama bank/channel) bukan rahasia — tampil apa adanya.
    """
    text = str(raw or "")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text[:32] or "••••"
    if len(digits) <= 4:
        return "••••"
    return f"••••{digits[-4:]}"


def format_rupiah(value: float | int | str | None) -> str:
    try:
        amount = int(float(str(value)))
    except (TypeError, ValueError):
        return "—"
    return "Rp" + f"{amount:,}".replace(",", ".")


_MULTIPLIER = (
    (re.compile(r"(?i)\b(\d[\d.,]*)\s*(jt|juta)\b"), 1_000_000),
    (re.compile(r"(?i)\b(\d[\d.,]*)\s*(rb|ribu|k)\b"), 1_000),
)


def _to_num(raw: str) -> float | None:
    text = raw.strip()
    if "," in text and "." in text:
        # Indonesia: titik ribuan, koma desimal → 2.500,5
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        # koma desimal → 2,5
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_rupiah(text: str) -> int | None:
    """Parse nominal bahasa Indonesia: '750 ribu', 'Rp2.750.000', '2,5 juta'."""
    lowered = str(text or "").lower().replace("rp", " ").replace("rupiah", " ")
    for pattern, factor in _MULTIPLIER:
        match = pattern.search(lowered)
        if match:
            number = _to_num(match.group(1))
            if number is not None:
                return int(number * factor)
    # nominal penuh bertitik: 2.750.000 / 2750000
    match = re.search(r"\b(\d{1,3}(?:\.\d{3})+|\d{4,})\b", lowered)
    if match:
        number = _to_num(match.group(1).replace(".", ""))
        return int(number) if number is not None else None
    return None


def contains_secret(text: str) -> bool:
    lowered = str(text or "")
    return any(p.search(lowered) for p in _SECRET_PATTERNS)


def redact(text: str) -> str:
    """Samarkan kandidat rahasia sebelum masuk ringkasan audit."""
    redacted = re.sub(r"\b\d{4,6}\b", "••••", str(text or ""))
    return redacted[:120]
