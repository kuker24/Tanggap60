"""Action Broker: GREEN / YELLOW / RED + registry yang divalidasi.

Tidak ada output model/LLM yang boleh langsung menjadi URL, selector,
perintah, query, atau aksi kredensial tanpa validasi deterministik di sini.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import HANDOFF_ALLOWLIST

# --- Registry target panduan UI (allowlist) -----------------------------------

GUIDE_TARGETS: frozenset[str] = frozenset(
    {
        "upload-evidence",
        "case-story",
        "evidence-list",
        "transaction-list",
        "review-facts",
        "confirm-mapping",
        "next-best-action",
        "readiness-summary",
        "approve-package",
        "workspace-open",
        "official-handoff",
        "receipt-form",
        "chat-panel",
    }
)

_DYNAMIC_TX = re.compile(r"^transaction-(ru_[0-9a-f]{12})(?:-(amount|destination|datetime))?$")

# --- Registry field Safe Workspace (allowlist) ---------------------------------

WORKSPACE_FIELDS: frozenset[str] = frozenset(
    {
        "victim_account",
        "victim_bank",
        "destination_account",
        "destination_bank",
        "destination_name",
        "amount",
        "date",
        "time",
        "chronology",
        "evidence_refs",
        "checklist",
    }
)

# --- Aksi ---------------------------------------------------------------------

GREEN_ACTIONS = frozenset(
    {
        "READ_CASE",
        "READ_READINESS",
        "EXPLAIN",
        "GUIDE_UI",
        "OPEN_INTERNAL",
        "PREPARE_WORKSPACE_PREVIEW",
    }
)

YELLOW_ACTIONS = frozenset(
    {
        "SET_UNIT_MAPPING",
        "OPEN_OFFICIAL",
    }
)

RED_MESSAGES: dict[str, str] = {
    "OTP": "Bagian ini meminta kode OTP. Demi keamanan, saya berhenti di sini. Silakan isi sendiri kode yang masuk ke ponsel Anda, lalu beri tahu saya jika sudah selesai.",
    "CREDENTIAL": "Saya tidak boleh menyentuh kata sandi, PIN, atau data login apa pun. Silakan isi sendiri di tempatnya, lalu beri tahu saya langkah berikutnya.",
    "CAPTCHA": "Verifikasi captcha harus Anda selesaikan sendiri. Saya tunggu — beri tahu saya jika sudah selesai.",
    "BANK_ACTION": "Saya tidak boleh menjalankan transaksi bank untuk Anda. Saya hanya menandai data yang perlu Anda bawa ke kanal resmi.",
    "AUTO_SUBMIT": "Dokumen belum dikirim ke mana pun, dan saya tidak akan mengirimkannya. Pengiriman laporan resmi tetap Anda lakukan sendiri lewat portal resmi.",
    "LEGAL_VERDICT": "Saya tidak boleh menetapkan siapa pelaku atau menyatakan seseorang bersalah. Saya hanya menyusun fakta dari bukti yang Anda kirim.",
    "EXTERNAL_SCRAPE": "Saya hanya boleh membuka portal resmi yang sudah diizinkan. Saya tidak mengambil data dari situs lain.",
    "KTP_AUTO": "Unggah identitas hanya Anda lakukan sendiri di portal resmi. Saya tidak mengunggah dokumen identitas untuk Anda.",
    "FINANCIAL_TX": "Saya tidak boleh memindahkan uang atau membayar apa pun. Mari lanjutkan menyiapkan datanya saja.",
}

GENERIC_RED = "Itu di luar batas aman saya. Saya berhenti di sini — tetapi Anda tetap bisa melanjutkan bagian lain secara manual."


@dataclass
class ProposedAction:
    action_id: str
    action_type: str
    risk: str
    summary: dict[str, Any]
    payload: dict[str, Any]
    expected_version: int


def action_id_for(
    case_id: str,
    action_type: str,
    payload: dict[str, Any],
    expected_version: int,
    secret_key: str | None = None,
) -> str:
    """HMAC-SHA256 stateless tamper-proof action identifier.
    
    Menggunakan server secret_key agar action_id tidak bisa dipalsukan
    oleh pihak yang hanya mengetahui case_id, payload, dan version.
    """
    import hmac

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    message = f"{case_id}|{action_type}|{canonical}|{expected_version}".encode()
    key = (secret_key or "tanggap60-action-broker-key").encode()
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"ag_{digest[:16]}"


def validate_guide_target(target: str, unit_ids: set[str]) -> str | None:
    """Kembalikan target bila ada di allowlist; None bila ditolak."""
    candidate = str(target or "").strip()
    if candidate in GUIDE_TARGETS:
        return candidate
    match = _DYNAMIC_TX.match(candidate)
    if match and match.group(1) in unit_ids:
        return candidate
    return None


def validate_url(url: str) -> str | None:
    """Hanya URL di HANDOFF_ALLOWLIST yang boleh dibuka agent."""
    candidate = str(url or "").strip().rstrip("/")
    allowed = {u.rstrip("/") for u in HANDOFF_ALLOWLIST}
    if candidate in allowed:
        return candidate + "/"
    return None


def red_message(category: str | None) -> str:
    return RED_MESSAGES.get(category or "", GENERIC_RED)
