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
        "processing-status",
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

# --- Live Rescue Mode: guidance plan contract ----------------------------------
# Frontend mengeksekusi langkah satu demi satu; tidak ada JS/selector/URL
# arbitrer dari model. Semua target & route tervalidasi deterministik.

GUIDE_STEP_TYPES: frozenset[str] = frozenset(
    {
        "STATUS",
        "SCROLL_TO",
        "SPOTLIGHT",
        "MOVE_POINTER",
        "CALLOUT",
        "OPEN_DISCLOSURE",
        "FOCUS",
        "NAVIGATE_INTERNAL",
        "WAIT_FOR_USER",
        "CLEAR_GUIDANCE",
        # Native Action Mode: langkah ACT — dieksekusi NativeActionBus,
        # bukan sekadar visual. OPEN/ FOCUS = GREEN auto; SET_DRAFT =
        # YELLOW prefill draf UI lokal, commit menunggu approval.
        "OPEN_TRANSACTION",
        "FOCUS_FIELD",
        "SET_DRAFT",
        "OPEN_EVIDENCE",
        "OPEN_WORKSPACE_VIEW",
    }
)

INTERNAL_ROUTES: frozenset[str] = frozenset(
    {
        "intake",
        "processing",
        "review",
        "readiness",
        "result",
        "approval",
        "artifacts",
        "receipt",
        "workspace",
    }
)

# Halaman kanonis tiap target statis; transaction-* dinamis selalu di review.
TARGET_PAGE: dict[str, str] = {
    "upload-evidence": "intake",
    "case-story": "intake",
    "evidence-list": "intake",
    "processing-status": "processing",
    "review-facts": "review",
    "confirm-mapping": "review",
    "next-best-action": "readiness",
    "transaction-list": "readiness",
    "readiness-summary": "readiness",
    "approve-package": "approval",
    "workspace-open": "workspace",
    "official-handoff": "artifacts",
    "receipt-form": "receipt",
    "chat-panel": "readiness",
}

_TARGET_STEPS = frozenset({"SCROLL_TO", "SPOTLIGHT", "MOVE_POINTER", "CALLOUT", "OPEN_DISCLOSURE", "FOCUS"})
_NATIVE_TX_STEPS = frozenset({"OPEN_TRANSACTION", "FOCUS_FIELD"})
# Aksi GREEN statis: target tetap di allowlist + halaman kanonisnya.
_NATIVE_STATIC_PAGES = {"OPEN_EVIDENCE": "intake", "OPEN_WORKSPACE_VIEW": "workspace"}
_MAX_PLAN_STEPS = 12
_MAX_STEP_TEXT = 200


def canonical_page_for(target: str) -> str | None:
    """Halaman kanonis sebuah target panduan; None bila target tak dikenal."""
    if target in TARGET_PAGE:
        return TARGET_PAGE[target]
    if _DYNAMIC_TX.match(target):
        return "review"
    return None


def validate_plan_step(step: Any, unit_ids: set[str]) -> dict[str, Any] | None:
    """Validasi satu langkah plan; kembalikan salinan bersih atau None (fail-closed)."""
    if not isinstance(step, dict):
        return None
    kind = str(step.get("type") or "")
    if kind not in GUIDE_STEP_TYPES:
        return None
    if kind in _NATIVE_STATIC_PAGES:
        target = validate_guide_target(str(step.get("target") or ""), unit_ids)
        if target is None or canonical_page_for(target) != _NATIVE_STATIC_PAGES[kind]:
            return None
        return {"type": kind, "target": target}
    if kind in _NATIVE_TX_STEPS:
        target = validate_guide_target(str(step.get("target") or ""), unit_ids)
        if target is None or not target.startswith("transaction-"):
            return None
        if kind == "FOCUS_FIELD":
            match = _DYNAMIC_TX.match(target)
            if not match or not match.group(2):
                return None
        return {"type": kind, "target": target}
    if kind == "SET_DRAFT":
        from app.agent.native_actions import NATIVE_FIELDS

        unit = str(step.get("unit") or "")
        field = str(step.get("field") or "")
        fact_id = str(step.get("fact_id") or "")
        label = str(step.get("label") or "")[:_MAX_STEP_TEXT]
        if not re.fullmatch(r"ru_[0-9a-f]{12}", unit) or unit not in unit_ids:
            return None
        if field not in NATIVE_FIELDS:
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", fact_id):
            return None
        if not label:
            return None
        return {"type": kind, "unit": unit, "field": field, "fact_id": fact_id, "label": label}
    if kind in _TARGET_STEPS:
        target = validate_guide_target(str(step.get("target") or ""), unit_ids)
        if target is None:
            return None
        clean: dict[str, Any] = {"type": kind, "target": target}
        if kind == "CALLOUT":
            title = str(step.get("title") or "")[:_MAX_STEP_TEXT]
            message = str(step.get("message") or "")[:_MAX_STEP_TEXT]
            if not message:
                return None
            clean["title"] = title
            clean["message"] = message
        return clean
    if kind == "NAVIGATE_INTERNAL":
        route = str(step.get("route") or "")
        if route not in INTERNAL_ROUTES:
            return None
        return {"type": kind, "route": route}
    if kind == "STATUS":
        message = str(step.get("message") or "")[:_MAX_STEP_TEXT]
        if not message:
            return None
        return {"type": kind, "message": message}
    # WAIT_FOR_USER dan CLEAR_GUIDANCE tidak membawa field
    return {"type": kind}


def build_plan(steps: Any, unit_ids: set[str]) -> list[dict[str, Any]]:
    """Bangun plan bersih; langkah invalid dibuang (fail-closed)."""
    if not isinstance(steps, list):
        return []
    clean: list[dict[str, Any]] = []
    for step in steps[:_MAX_PLAN_STEPS]:
        valid = validate_plan_step(step, unit_ids)
        if valid is not None:
            clean.append(valid)
    return clean

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
