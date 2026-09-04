"""Native Action registry: semantic, allowlisted, tervalidasi.

Kontrak MASTER §3-§5, §24, §44: model/LLM TIDAK PERNAH menghasilkan
JavaScript, CSS selector, URL, atau perintah DOM arbitrer. Server hanya
menyusun dict aksi terstruktur:

    {"action": "OPEN_TRANSACTION", "target": "ru_abcd...", "risk": "GREEN"}

Frontend NativeActionBus me-resolve aksi lewat registry miliknya sendiri
(data-guide-id / data-agent-field yang sudah ada di template).

Pembedaan §48 ditegakkan di level hasil:
- GUIDE  = tunjukkan di mana (langkah visual lama).
- PREPARE = ubah draf UI lokal, tanpa commit server (SET_DRAFT).
- ACT    = eksekusi GREEN yang diizinkan / YELLOW yang sudah di-approve.
"""

from __future__ import annotations

import re
from typing import Any

# --- Registry ----------------------------------------------------------------

# name -> {risk, butuh unit target, butuh fact kandidat}
REGISTRY: dict[str, dict[str, Any]] = {
    # GREEN: navigasi/fokus/buka di dalam web sendiri — auto execute.
    "OPEN_TRANSACTION": {"risk": "GREEN", "needs_unit": True, "needs_fact": False},
    "FOCUS_TX_FIELD": {"risk": "GREEN", "needs_unit": True, "needs_fact": False, "needs_field": True},
    "OPEN_EVIDENCE": {"risk": "GREEN", "needs_unit": False, "needs_fact": False},
    "OPEN_WORKSPACE_VIEW": {"risk": "GREEN", "needs_unit": False, "needs_fact": False},
    # YELLOW: ubah draf UI lokal saja; commit server menunggu approval manusia.
    "SET_DRAFT": {"risk": "YELLOW", "needs_unit": True, "needs_fact": True, "needs_field": True},
}

GREEN_NATIVE = frozenset(n for n, r in REGISTRY.items() if r["risk"] == "GREEN")
YELLOW_NATIVE = frozenset(n for n, r in REGISTRY.items() if r["risk"] == "YELLOW")

# RED: tidak pernah dieksekusi sebagai native action (defense-in-depth;
# classifier intents.py sudah menolak pola ini sebelum sampai ke sini).
RED_NATIVE = frozenset(
    {
        "FILL_PASSWORD",
        "FILL_OTP",
        "FILL_PIN",
        "SOLVE_CAPTCHA",
        "BANK_TRANSFER",
        "AUTO_LOGIN",
        "SUBMIT_OFFICIAL",
        "UPLOAD_KTP",
        "LEGAL_VERDICT",
    }
)

# Field semantik yang boleh difokus/di-draf (bukan nama input DOM).
NATIVE_FIELDS = frozenset({"amount", "destination", "datetime"})

# field -> tipe kandidat fakta yang sah (lihat build_agent_context).
FIELD_CANDIDATE_TYPES: dict[str, frozenset[str]] = {
    "amount": frozenset({"AMOUNT"}),
    "destination": frozenset({"ACCOUNT", "PJP"}),
    "datetime": frozenset({"DATETIME"}),
}

_ID_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_UNIT_SHAPE = re.compile(r"^ru_[0-9a-f]{12}$")
_MAX_LABEL = 200

# Pola yang jelas-jelas bukan aksi native (JS / selector / URL / perintah).
_FORBIDDEN_FRAGMENTS = (
    "document.",
    "window.",
    "eval(",
    "function(",
    "=>",
    "queryselector",
    "<script",
    "javascript:",
    "http://",
    "https://",
    "SELECT ",
    "DROP ",
    "--",
)


def _looks_hostile(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(frag.lower() in lowered for frag in _FORBIDDEN_FRAGMENTS)


def validate_native_action(action: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    """Validasi satu aksi native; kembalikan salinan bersih atau None (fail-closed).

    - nama harus di REGISTRY (unknown -> tolak, §44);
    - risk tidak boleh di-spoof (harus sama dengan registry);
    - target unit harus milik kasus ini (cross-case -> tolak);
    - fact_id harus kandidat bertipe cocok dari unit itu (invalid -> tolak);
    - RED / fragmen JS-selector-URL -> tolak.
    """
    if not isinstance(action, dict):
        return None
    name = str(action.get("action") or "")
    if name in RED_NATIVE:
        return None
    spec = REGISTRY.get(name)
    if spec is None:
        return None
    if str(action.get("risk") or "") != spec["risk"]:
        return None

    unit_ids = set(context.get("unit_ids") or [])
    clean: dict[str, Any] = {"action": name, "risk": spec["risk"]}

    if spec.get("needs_unit"):
        target = str(action.get("target") or "")
        if not _UNIT_SHAPE.match(target) or target not in unit_ids:
            return None
        clean["target"] = target

    if spec.get("needs_field"):
        field = str(action.get("field") or "")
        if field not in NATIVE_FIELDS:
            return None
        clean["field"] = field

    if spec.get("needs_fact"):
        fact_id = str(action.get("fact_id") or "")
        if not _ID_SHAPE.match(fact_id) or _looks_hostile(fact_id):
            return None
        unit = next((u for u in context.get("units", []) if u.get("unit_id") == clean.get("target")), None)
        if unit is None:
            return None
        allowed_types = FIELD_CANDIDATE_TYPES[clean["field"]]
        hit = any(
            c.get("fact_id") == fact_id and c.get("type") in allowed_types
            for c in unit.get("candidates", [])
            if isinstance(c, dict)
        )
        if not hit:
            return None
        clean["fact_id"] = fact_id

    label = str(action.get("label") or "")[:_MAX_LABEL]
    if label:
        if _looks_hostile(label):
            return None
        clean["label"] = label
    return clean


def action_result(
    action: str,
    status: str,
    *,
    changed: bool = False,
    requires_human: bool = False,
    message: str = "",
    reason: str | None = None,
) -> dict[str, Any]:
    """Kontrak hasil aksi native §49: COMPLETED / WAITING_APPROVAL / DENIED."""
    if status not in {"COMPLETED", "WAITING_APPROVAL", "DENIED"}:
        status = "DENIED"
        reason = reason or "STATUS_UNKNOWN"
    body: dict[str, Any] = {
        "action": action,
        "status": status,
        "changed": bool(changed),
        "requires_human": bool(requires_human),
        "message": str(message or "")[:_MAX_LABEL],
    }
    if reason:
        body["reason"] = str(reason)[:_MAX_LABEL]
    return body
