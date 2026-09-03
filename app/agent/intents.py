"""Klasifikasi intent deterministik — bukan system prompt panjang.

Aturan: pola kata kunci bahasa Indonesia yang kecil dan bisa diaudit.
Hermes CLI/HTTP hanya dipakai untuk memilih *tool* bila tersedia dan
intent tidak jelas; kalimat jawaban tidak pernah dibuat model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.formatting import contains_secret, parse_rupiah

# --- RED: pola yang selalu ditolak Action Broker ---------------------------

RED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OTP", re.compile(r"(?i)\b(otp|one[- ]?time|kode verifikasi|kode sms|token sms)\b")),
    ("CREDENTIAL", re.compile(r"(?i)\b(kata sandi|password|sandi|username|user ?id|pin( atm| mobile| banking)?|kredensial|login|masuk akun|sign in)\b")),
    ("CAPTCHA", re.compile(r"(?i)\b(captcha|capcay|verifikasi saya bukan robot|i'm not a robot)\b")),
    ("BANK_ACTION", re.compile(r"(?i)\b(mbanking|m-banking|mobile banking|internet banking|klik ?bca|brimo|livin)\b.*\b(bayar|transfer|kirim uang)\b")),
    ("AUTO_SUBMIT", re.compile(r"(?i)\b((kirim|kirimin|submit|laporkan?)(kan)?\b.{0,30}?\b(langsung|otomatis|tanpa tanya)|klik submit tanpa tanya)\b")),
    ("LEGAL_VERDICT", re.compile(r"(?i)\b(anggap .* (penipu|pelaku|jahat|bersalah)|nyatakan .* (penipu|bersalah)|vonis|tuduh)\b")),
    ("EXTERNAL_SCRAPE", re.compile(r"(?i)\b(buka(kan)? (website|situs|web|link|url|tautan) lain|ambil(kan)? data dari|scrape|crawl|bypass|bobol|hack|retas)\b")),
    ("KTP_AUTO", re.compile(r"(?i)\b(unggah(kan)? ktp|upload ktp|foto ktp saya)\b.*\b(otomatis|langsung|untuk saya)\b")),
    ("FINANCIAL_TX", re.compile(r"(?i)\b(bayar(kan)? (denda|tagihan|uang)|transfer(kan)? uang|tarik tunai)\b.*\b(untuk saya|atas nama saya|sekarang)\b")),
)

# --- Intent biasa ------------------------------------------------------------

_INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GREETING", re.compile(r"(?i)^(halo|hai|hi|pagi|siang|sore|malam|assalamu|permisi|tes|test|halo+)\b")),
    ("ASK_NEXT", re.compile(r"(?i)\b(harus (ngapain|apa)|lakukan sekarang|langkah (selanjutnya|berikutnya)|apa yang (harus|sebaiknya|perlu) (saya |aku )?(lakukan|kerjakan)|tindakan (selanjutnya|berikutnya)|next|gimana (lagi|selanjutnya))\b")),
    ("SHOW_MISSING", re.compile(r"(?i)\b(tunjukin|tunjukkan|tampilkan|mana )?.*\b(kurang|belum (lengkap|isi|diisi|ada)|hilang|masih perlu|perlu dilengkapi|apa saja yang kurang)\b")),
    ("SHOW_PROBLEM", re.compile(r"(?i)\b(mana )?.*\b(bermasalah|bentrok|konflik|ambigu|belum jelas|perlu dipastikan|perlu dikonfirmasi|ditandai|tandai)\b")),
    ("CONFUSED", re.compile(r"(?i)\b(saya |aku )?(bingung|tidak (paham|mengerti|ngerti)|gak (paham|ngerti)|tolong (jelaskan|bantu)|help)\b")),
    ("PREPARE_REPORT", re.compile(r"(?i)\b(bantu )?(siapkan|buat(kan)?|susun) (laporannya?|dokumen|paket|berkas)|(laporan|dokumen|paket).*?\b(siap(kan|in)?)\b")),
    ("OPEN_WORKSPACE", re.compile(r"(?i)\b(buka |tampilkan )?(workspace|ruang kerja|formulir persiapan|simulasi (form|persiapan)|lembar persiapan)\b")),
    ("EXPLAIN_READINESS", re.compile(r"(?i)\b(kenapa|mengapa|kok) .*?\b(belum siap|tidak siap|gak siap|kurang|belum bisa)\b")),
    ("EXPLAIN_PACKAGE", re.compile(r"(?i)\b(apa (saja |aja )?(yang )?(akan dikirim|dikirim|di dalam paket|isinya)|isi paket|apa isi|yang dikirim (nanti|ke|apa))\b")),
    ("OPEN_OFFICIAL", re.compile(r"(?i)\b(buka(kan)? )?(portal resmi|laman resmi|situs resmi|iasc|ojk|kanal resmi)\b")),
    ("CONFIRM_YES", re.compile(r"(?i)^(ya|iya|betul|benar|oke|ok|setuju|simpan|lakukan|lanjut|ya,? simpan)\b")),
    ("CONFIRM_NO", re.compile(r"(?i)^(bukan|jangan|tidak|gak|nggak|batal|jangan simpan)\b")),
    ("EXPLAIN_STATE", re.compile(r"(?i)\b(apa yang terjadi|status (kasus|saya)|kondisi (kasus|saya)|ringkas(an|kan)|rangkum|rekap)\b")),
)

_DEICTIC = re.compile(r"(?i)\b(yang ini|ini |tadi|tersebut|itu |transaksi (ini|itu|tersebut)|nominal (ini|itu))\b")


@dataclass
class Intent:
    kind: str
    amount: int | None = None
    red_category: str | None = None
    confidence: str = "high"
    extra: dict[str, Any] = field(default_factory=dict)


def classify(text: str) -> Intent:
    """Klasifikasikan pesan pengguna. Selalu kembalikan satu Intent."""
    raw = str(text or "").strip()
    if not raw:
        return Intent(kind="GREETING")
    for category, pattern in RED_PATTERNS:
        if pattern.search(raw):
            return Intent(kind="RED", red_category=category)
    # Koreksi nominal: deiktik + angka, mis. "Yang ini 750 ribu."
    amount = parse_rupiah(raw)
    if amount is not None and _DEICTIC.search(raw):
        return Intent(kind="CONFIRM_MAPPING_VALUE", amount=amount)
    if amount is not None and re.search(r"(?i)\b(benar|betul|yang benar|seharusnya|harusnya)\b", raw):
        return Intent(kind="CONFIRM_MAPPING_VALUE", amount=amount)
    for kind, pattern in _INTENT_RULES:
        if pattern.search(raw):
            return Intent(kind=kind)
    if amount is not None:
        return Intent(kind="CONFIRM_MAPPING_VALUE", amount=amount, confidence="low")
    if contains_secret(raw):
        return Intent(kind="RED", red_category="CREDENTIAL")
    return Intent(kind="UNKNOWN", confidence="low")
