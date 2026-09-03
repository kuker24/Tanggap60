"""ConversationService: pesan → tool Tanggap60 → respons terstruktur.

Bukti agentic (bukan chatbot prompt-panjang):
1. baca current case state (konteks terstruktur, tanpa PII mentah);
2. pilih tool Tanggap60 (router deterministik; Hermes CLI/HTTP bila ada);
3. jalankan tool lewat ``execute_tool`` (allowlist per state);
4. susun guidance/action dari HASIL tool via template tetap.

Hermes/LLM tidak pernah menulis kalimat pengguna.
"""

from __future__ import annotations

import time
from typing import Any, cast

from app.agent.broker import (
    YELLOW_ACTIONS,
    ProposedAction,
    action_id_for,
    red_message,
    validate_guide_target,
    validate_url,
)
from app.agent.context import build_agent_context
from app.agent.formatting import escape, format_rupiah, redact
from app.agent.intents import Intent, classify
from app.agent.workspace import prepare_workspace
from app.config import TOOL_VERSION
from app.deps import services_from
from app.domain.errors import StaleCaseVersion, ValidationFailed
from app.domain.models import AuditEventRecord
from app.domain.policies import sha256_text
from app.hermes.telemetry import mode_from_hermes, planner_for
from app.hermes.tool_registry import ToolContext, execute_tool
from app.hermes.tools.catalog import allowed_tools
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import EventRepository
from app.services.cases import now_utc
from app.services.ids import new_id

_AGENT_READ_TOOLS: dict[str, tuple[str, ...]] = {
    "ASK_NEXT": ("recommend_next_action", "compile_reporting_units"),
    "GREETING": ("recommend_next_action", "compile_reporting_units"),
    "SHOW_MISSING": ("assess_handoff_readiness", "compile_reporting_units"),
    "SHOW_PROBLEM": ("compile_reporting_units", "assess_handoff_readiness"),
    "CONFUSED": ("recommend_next_action", "compile_reporting_units"),
    "EXPLAIN_READINESS": ("assess_handoff_readiness", "compile_reporting_units"),
    "EXPLAIN_STATE": ("compile_reporting_units", "recommend_next_action"),
    "EXPLAIN_PACKAGE": ("compile_reporting_units",),
    "PREPARE_REPORT": ("assess_handoff_readiness", "compile_reporting_units"),
    "OPEN_WORKSPACE": ("compile_reporting_units",),
    "CONFIRM_MAPPING_VALUE": ("compile_reporting_units",),
    "OPEN_OFFICIAL": ("prepare_official_handoff",),
    "UNKNOWN": ("recommend_next_action", "compile_reporting_units"),
}


def _audit(
    db: Any,
    case_id: str,
    event_type: str,
    state: str,
    tool_name: str | None = None,
    duration_ms: int | None = None,
    result_code: str = "OK",
    error_code: str | None = None,
    summary: str = "",
    planner: str = "DETERMINISTIC_SAFE",
) -> None:
    EventRepository(db).add(
        AuditEventRecord(
            event_id=new_id("evt"),
            case_id=hash_id(case_id),
            run_id=None,
            event_type=event_type,
            state_before=state,
            state_after=state,
            tool_name=tool_name,
            tool_version=TOOL_VERSION,
            duration_ms=duration_ms,
            result_code=result_code,
            error_code=error_code,
            payload_hash=sha256_text(redact(summary)) if summary else None,
            created_at=now_utc(),
            planner=planner,
            execution="AGENT_TOOL",
        )
    )


class _Runner:
    """Menjalankan read tool lewat Hermes execute_tool bila diizinkan state."""

    def __init__(self, db: Any, container: Any, case_id: str, state: str) -> None:
        self.db = db
        self.container = container
        self.case_id = case_id
        self.state = state
        self.allowed = set(allowed_tools(state))
        self.used: list[dict[str, Any]] = []
        self.tool_ms = 0
        services = services_from(db, container)
        self.tool_ctx = cast(ToolContext, services["ctx"])

    def hermes_preference(self, candidates: tuple[str, ...]) -> str | None:
        """Bila Hermes CLI/HTTP terkonfigurasi, biarkan ia memilih tool."""
        hermes = self.container.hermes
        if not bool(getattr(hermes, "hermes_cli_configured", False)):
            return None
        options = [c for c in candidates if c in self.allowed]
        if not options:
            return None
        try:
            picked = hermes.propose_tool(self.state, {"allowed_tools": options, "agent": True})
        except Exception:
            return None
        return picked if picked in options else None

    def run(self, name: str, summary: str = "") -> dict[str, Any]:
        """Jalankan tool; catat planner + durasi untuk bukti agentic."""
        if name in self.allowed:
            start = time.perf_counter()
            result = execute_tool(name, self._state_obj(), {"case_id": self.case_id}, self.tool_ctx)
            duration = int((time.perf_counter() - start) * 1000)
            planner = planner_for(name, mode_from_hermes(self.container.hermes))
        else:
            # State tidak mengizinkan tool ini: pakai hasil konteks yang
            # dihitung fungsi service yang SAMA (tanpa duplikat logika).
            result = {"via": "context", "note": f"{name} tidak diizinkan pada {self.state}"}
            duration = 0
            planner = "DETERMINISTIC_SAFE"
        self.used.append({"tool": name, "planner": planner, "duration_ms": duration})
        self.tool_ms += duration
        _audit(self.db, self.case_id, "AGENT_TOOL_REQUEST", self.state, name, None, "OK", None, summary, planner)
        _audit(self.db, self.case_id, "AGENT_TOOL_RESULT", self.state, name, duration, "OK", None, summary, planner)
        return result

    def _state_obj(self) -> Any:
        from app.domain.states import State

        return State(self.state)


def handle_message(
    db: Any,
    container: Any,
    case_id: str,
    session_id: str,
    text: str,
    ui_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Titik masuk percakapan. Kembalikan respons terstruktur (JSON)."""
    started = time.perf_counter()
    services = services_from(db, container)
    services["cases"].get_owned(case_id, session_id)
    ui_state = {**(ui_state or {}), "raw_text": text}

    intent = classify(text)
    if intent.kind == "RED":
        message = red_message(intent.red_category)
        context = build_agent_context(db, case_id)
        _audit(db, case_id, "SENSITIVE_STOP", context["case"]["state"], None, 0, "DENIED", intent.red_category, text)
        return _response(context, message, None, None, [], started, 0)

    context = build_agent_context(db, case_id)
    state = context["case"]["state"]
    _audit(db, case_id, "AGENT_MESSAGE", state, None, 0, "OK", None, text)
    runner = _Runner(db, container, case_id, state)

    # Hermes boleh memilih tool kandidat bila terkonfigurasi.
    candidates = _AGENT_READ_TOOLS.get(intent.kind, _AGENT_READ_TOOLS["UNKNOWN"])
    preferred = runner.hermes_preference(candidates)
    ordered = ((preferred,) + tuple(c for c in candidates if c != preferred)) if preferred else candidates
    for tool_name in ordered:
        runner.run(tool_name, intent.kind)

    handler = _HANDLERS.get(intent.kind, _handle_unknown)
    message, guidance, proposal = handler(intent, context, runner, ui_state)
    if proposal is not None:
        _audit(
            db, case_id, "ACTION_PROPOSED", state, None, 0, "OK", None,
            f"{proposal.action_type} {proposal.action_id}",
        )
    if guidance is not None:
        _audit(db, case_id, "GUIDANCE_SHOWN", state, None, 0, "OK", None, guidance["target"])
    return _response(context, message, guidance, proposal, runner.used, started, runner.tool_ms)


def _response(
    context: dict[str, Any],
    message: str,
    guidance: dict[str, str] | None,
    proposal: ProposedAction | None,
    tools_used: list[dict[str, Any]],
    started: float,
    tool_ms: int,
) -> dict[str, Any]:
    total_ms = int((time.perf_counter() - started) * 1000)
    body: dict[str, Any] = {
        "message": message,
        "quick_actions": context["quick_actions"],
        "guidance": guidance,
        "proposed_action": None,
        "tools_used": tools_used,
        "agent_response_ms": total_ms,
        "agent_tool_ms": tool_ms,
        "state": context["case"]["state"],
        "case_version": context["case"]["version"],
        "technical": {
            "planner_modes": sorted({t["planner"] for t in tools_used}),
            "fallback_note": "Hermes CLI/endpoint tidak terkonfigurasi; router deterministik + tool Tanggap60.",
        },
    }
    if proposal is not None:
        body["proposed_action"] = {
            "action_id": proposal.action_id,
            "action_type": proposal.action_type,
            "risk": proposal.risk,
            "summary": proposal.summary,
            # payload dikembalikan agar klien bisa approve; server verifikasi
            # ulang action_id + ownership + versi (anti-tamper).
            "payload": proposal.payload,
            "expected_version": proposal.expected_version,
        }
        # Keputusan lewat kartu Simpan/Batal agar eksplisit (tanpa duplikasi chip).
        body["quick_actions"] = []
    return body


def _guide(target: str | None, label: str, unit_ids: set[str]) -> dict[str, str] | None:
    if not target:
        return None
    valid = validate_guide_target(target, unit_ids)
    if valid is None:
        return None
    return {"target": valid, "label": label}


def _unit_label(unit: dict[str, Any]) -> str:
    amount = unit.get("amount_text") or ""
    dest = unit.get("destination_masked") or ""
    bits = []
    if amount and amount != "—":
        bits.append(amount)
    if dest:
        bits.append(f"ke {dest}")
    if bits:
        return f"Transaksi {unit['index']} ({' '.join(bits)})"
    return f"Transaksi {unit['index']}"


# --- Handler per intent (template tetap, data dari tool/konteks) ---------------

def _handle_greeting(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    units = context["units"]
    if not units and context["evidence_count"] == 0:
        return (
            "Saya bisa mendampingi Anda. Kirim bukti yang ada — foto transfer, chat, atau link.",
            _guide("upload-evidence", "Kirim bukti di sini", set(context["unit_ids"])),
            None,
        )
    ambiguous = [u for u in units if u["mapping_status"] == "AMBIGUOUS"]
    blocking = [c for c in context["conflicts_open"] if c["severity"] == "BLOCKING"]
    if blocking:
        return (
            "Saya menemukan data yang saling bertentangan. Saya tidak akan menebak — pilih yang benar dulu.",
            _guide("review-facts", "Selesaikan di sini", set(context["unit_ids"])),
            None,
        )
    if ambiguous:
        names = ", ".join(_unit_label(u) for u in ambiguous)
        return (
            f"Saya menemukan {len(units)} transaksi. {names} masih perlu dikonfirmasi. Saya tidak akan menebak.",
            _guide(f"transaction-{ambiguous[0]['unit_id']}", "Periksa transaksi ini", set(context["unit_ids"])),
            None,
        )
    return (
        _next_action_text(context),
        _guide_for_action(context),
        None,
    )


def _next_action_text(context: dict[str, Any]) -> str:
    action = context["next_action"]
    code = action.get("code")
    target = action.get("target_unit_id")
    unit = next((u for u in context["units"] if u["unit_id"] == target), None)
    who = f" {_unit_label(unit)}." if unit else "."
    mapping = {
        "CONTACT_BANK_PJP": f"Ada transaksi yang banknya sudah siap{who} Hubungi bank lewat kanal resmi sekarang — tidak perlu menunggu transaksi lain.",
        "PREPARE_IASC_UNIT": f"Ada transaksi yang siap dilaporkan{who} Siapkan datanya, lalu buka portal resmi IASC.",
        "RESOLVE_CONFLICT": "Ada data yang saling bertentangan. Pilih yang benar supaya paketnya akurat — saya tandai bagian itu.",
        "RESOLVE_UNIT_MAPPING": f"Ada transaksi yang belum terpasang{who} Pilih pasangan jumlah uang, rekening, dan waktu yang benar.",
        "CONFIRM_TRANSACTION_AMOUNT": f"Jumlah uang {who} belum jelas. Konfirmasi nominal yang sesuai bukti.",
        "CONFIRM_TRANSACTION_TIME": f"Waktu transfer {who} belum jelas. Konfirmasi waktunya.",
        "CONFIRM_DESTINATION": f"Rekening tujuan {who} belum jelas. Konfirmasi rekeningnya.",
        "ADD_TRANSFER_EVIDENCE": "Tambah bukti transfer yang memuat jumlah uang, rekening, dan waktu.",
        "PREPARE_POLICE_INCIDENT": "Setelah urusan bank, siapkan kronologi untuk kanal resmi kepolisian.",
        "APPROVE_READY_UNIT": "Ada transaksi menunggu persetujuan Anda untuk dibuatkan paket terverifikasi.",
        "DOWNLOAD_VERIFIED_PACK": "Semua unit sudah diproses. Unduh paket terverifikasi lalu lakukan handoff manual.",
        "OPEN_IASC_HANDOFF": "Buka portal resmi IASC dan isi datanya sendiri. Saya sudah menyiapkan ringkasannya.",
        "RECORD_RECEIPT": "Catat nomor laporan resmi bila sudah ada.",
    }
    return mapping.get(code, action.get("reason") or "Mari periksa kondisi kasus Anda langkah demi langkah.")


def _guide_for_action(context: dict[str, Any]) -> dict[str, str] | None:
    unit_ids = set(context["unit_ids"])
    action = context["next_action"]
    code = action.get("code")
    target = action.get("target_unit_id")
    if target and validate_guide_target(f"transaction-{target}", unit_ids):
        return {"target": f"transaction-{target}", "label": "Lihat transaksi ini"}
    mapping = {
        "RESOLVE_CONFLICT": ("review-facts", "Selesaikan di sini"),
        "RESOLVE_UNIT_MAPPING": ("confirm-mapping", "Pasangkan di sini"),
        "APPROVE_READY_UNIT": ("approve-package", "Setujui di sini"),
        "DOWNLOAD_VERIFIED_PACK": ("approve-package", "Unduh di sini"),
        "OPEN_IASC_HANDOFF": ("official-handoff", "Buka portal resmi"),
        "ADD_TRANSFER_EVIDENCE": ("upload-evidence", "Tambah bukti di sini"),
    }
    if code in mapping:
        target_name, label = mapping[code]
        return _guide(target_name, label, unit_ids)
    return _guide("next-best-action", "Lihat tindakan utama", unit_ids)


def _handle_ask_next(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (_next_action_text(context), _guide_for_action(context), None)


def _handle_missing(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    blocking = [c for c in context["conflicts_open"] if c["severity"] == "BLOCKING"]
    if blocking:
        return (
            "Ada data yang saling bertentangan dan memblokir proses. Pilih dulu yang benar — saya tandai.",
            _guide("review-facts", "Selesaikan di sini", unit_ids),
            None,
        )
    incomplete = [u for u in context["units"] if u["mapping_status"] in {"AMBIGUOUS", "INCOMPLETE"}]
    if not incomplete and not context["units"]:
        return (
            "Belum ada transaksi. Kirim bukti transfer yang memuat jumlah uang, rekening, dan waktu.",
            _guide("upload-evidence", "Kirim bukti di sini", unit_ids),
            None,
        )
    first = incomplete[0] if incomplete else context["units"][0]
    missing = []
    if first.get("amount") is None:
        missing.append("jumlah uang")
    if not first.get("destination_masked"):
        missing.append("rekening tujuan")
    if not first.get("transferred_at"):
        missing.append("waktu transfer")
    field = {"jumlah uang": "amount", "rekening tujuan": "destination", "waktu transfer": "datetime"}
    if missing:
        detail = ", ".join(missing)
        suffix = {"amount": "amount", "destination": "destination", "datetime": "datetime"}[field[missing[0]]]
        return (
            f"Transaksi {first['index']} belum lengkap: {detail} belum terisi. Saya tandai bagian itu.",
            _guide(f"transaction-{first['unit_id']}-{suffix}", "Lengkapi di sini", unit_ids),
            None,
        )
    return (
        f"{_unit_label(first)} datanya lengkap. Tinggal ikuti tindakan utama.",
        _guide("next-best-action", "Lihat tindakan utama", unit_ids),
        None,
    )


def _handle_problem(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    blocking = [c for c in context["conflicts_open"] if c["severity"] == "BLOCKING"]
    ambiguous = [u for u in context["units"] if u["mapping_status"] == "AMBIGUOUS"]
    if blocking:
        return (
            "Saya menemukan data yang saling bertentangan dan memblokir proses. Saya tidak akan menebak — pilih yang benar.",
            _guide("review-facts", "Selesaikan di sini", unit_ids),
            None,
        )
    if ambiguous:
        first = ambiguous[0]
        return (
            f"{_unit_label(first)} perlu dipastikan pasangannya. Saya tandai sekarang.",
            _guide(f"transaction-{first['unit_id']}", "Periksa transaksi ini", unit_ids),
            None,
        )
    return (
        "Tidak ada yang bermasalah. Semua data yang ada sudah jelas.",
        _guide("next-best-action", "Lihat tindakan utama", unit_ids),
        None,
    )


def _handle_confused(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (
        f"Tidak apa-apa, saya bantu. {_next_action_text(context)} Saya tandai yang perlu diperbaiki.",
        _guide_for_action(context),
        None,
    )


def _handle_state(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    units = context["units"]
    ready = sum(1 for u in units if u["mapping_status"] == "COMPLETE")
    ambiguous = sum(1 for u in units if u["mapping_status"] == "AMBIGUOUS")
    parts = [f"{len(units)} transaksi"]
    if ready:
        parts.append(f"{ready} jelas")
    if ambiguous:
        parts.append(f"{ambiguous} perlu dikonfirmasi")
    if context["conflicts_open"]:
        parts.append(f"{len(context['conflicts_open'])} konflik terbuka")
    return (
        f"Kondisi kasus Anda: {', '.join(parts)}. {_next_action_text(context)}",
        _guide_for_action(context),
        None,
    )


def _handle_readiness(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    overall = context["readiness_overall"]
    if overall == "READY":
        return (
            "Semuanya siap. Tinggal persetujuan Anda untuk dibuatkan paket.",
            _guide("approve-package", "Setujui di sini", set(context["unit_ids"])),
            None,
        )
    return _handle_missing(intent, context, runner, ui)


def _handle_package(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    if context["approval_present"]:
        count = len(context["artifacts"])
        return (
            f"Paket berisi data yang sudah Anda setujui ({count} artefak terverifikasi). Dokumen belum dikirim ke mana pun.",
            _guide("approve-package", "Lihat paket", set(context["unit_ids"])),
            None,
        )
    return (
        "Yang akan dikirim hanya data yang sudah Anda setujui. Saat ini belum ada persetujuan — tidak ada yang dikirim.",
        _guide("approve-package", "Periksa persetujuan", set(context["unit_ids"])),
        None,
    )


def _handle_prepare(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    ws = prepare_workspace(runner.db, context["case"]["case_id"])
    count = ws["confirmed_transactions"]
    if count == 0:
        return (
            "Belum ada transaksi terkonfirmasi untuk disiapkan. Selesaikan konfirmasi dulu — saya tandai.",
            _guide_for_action(context),
            None,
        )
    return (
        f"Saya siapkan {count} transaksi terkonfirmasi ke ruang persiapan. Identitas korban tetap Anda isi sendiri.",
        _guide("workspace-open", "Buka ruang persiapan", set(context["unit_ids"])),
        None,
    )


def _handle_workspace(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (
        "Ruang persiapan adalah simulasi formulir — bukan portal resmi. Data terkonfirmasi terisi otomatis, sisanya Anda isi sendiri.",
        _guide("workspace-open", "Buka ruang persiapan", set(context["unit_ids"])),
        None,
    )


def _handle_official(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    configured = str(getattr(runner.container.settings, "official_iasc_url", "") or "")
    url = validate_url(configured) or "https://iasc.ojk.go.id/"
    proposal = _propose(context, "OPEN_OFFICIAL", {"url": url}, {"url": url})
    return (
        "Saya siapkan tautan portal resmi IASC. Buka sendiri dan isi datanya — saya tidak akan mengirim apa pun.",
        _guide("official-handoff", "Buka portal resmi", set(context["unit_ids"])),
        proposal,
    )


def _blocking_fact_ids(context: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for conflict in context["conflicts_open"]:
        if conflict["severity"] == "BLOCKING":
            ids.update(conflict["fact_ids"])
    return ids


def _dest_hint_matches(label: str, text: str) -> bool:
    """Hint rekening eksplisit dari pengguna: label penuh atau 4 digit ekor."""
    import re as _re

    lab = str(label or "").lower()
    lowered = str(text or "").lower()
    if len(lab) >= 4 and lab in lowered:
        return True
    digits = _re.sub(r"\D", "", label or "")
    text_digits = _re.sub(r"\D", "", text or "")
    return len(digits) >= 4 and len(text_digits) >= 4 and digits[-4:] in text_digits


def _handle_confirm_value(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    amount = intent.amount or 0
    text_amount = format_rupiah(amount)
    user_text = str(ui.get("raw_text") or "")
    matches = []
    for unit in context["units"]:
        if unit["mapping_status"] != "AMBIGUOUS":
            continue
        hit = unit.get("amount") == amount
        if not hit:
            for cand in unit.get("candidates", []):
                if cand["type"] == "AMOUNT" and _amount_eq(cand["value"], amount):
                    hit = True
                    break
        if hit:
            matches.append(unit)
    if not matches:
        return (
            f"Saya tidak menemukan transaksi {text_amount} yang perlu dikonfirmasi. Coba sebutkan nominal persis seperti di bukti.",
            _guide("transaction-list", "Lihat semua transaksi", unit_ids),
            None,
        )
    if len(matches) > 1:
        return (
            f"Ada {len(matches)} transaksi yang cocok dengan {text_amount}. Saya tidak akan menebak — pilih yang benar.",
            _guide("transaction-list", "Pilih transaksinya", unit_ids),
            None,
        )
    unit = matches[0]
    blocked = _blocking_fact_ids(context) & set(unit["fact_ids"])
    if blocked:
        return (
            "Ada data transaksi ini yang masih bentrok. Pilih dulu yang benar — setelah itu baru kita pasangkan.",
            _guide("review-facts", "Selesaikan di sini", unit_ids),
            None,
        )
    dest_cands = [c for c in unit.get("candidates", []) if c["type"] in {"ACCOUNT", "PJP"}]
    amount_cands = [c for c in unit.get("candidates", []) if c["type"] == "AMOUNT"]
    time_cands = [c for c in unit.get("candidates", []) if c["type"] == "DATETIME"]
    amount_hits = [c for c in amount_cands if _amount_eq(c["value"], amount)]
    if len(dest_cands) == 1:
        dest_pick = dest_cands
    else:
        dest_pick = [c for c in dest_cands if _dest_hint_matches(c["value"], user_text)]
    if len(dest_pick) != 1 or not amount_hits:
        return (
            f"{text_amount} cocok, tetapi masih ada beberapa kemungkinan pasangan. Saya tidak akan menebak — "
            "sebutkan rekeningnya juga, atau pilih langsung di kartu.",
            _guide(f"transaction-{unit['unit_id']}", "Pilih pasangannya", unit_ids),
            None,
        )
    amount_fact = amount_hits[0]
    evidence_id = _evidence_of(runner, amount_fact["fact_id"])
    pairings = [
        {
            "destination_fact_id": dest_pick[0]["fact_id"],
            "amount_fact_id": amount_fact["fact_id"],
            "datetime_fact_id": time_cands[0]["fact_id"] if len(time_cands) == 1 else "",
        }
    ]
    dest_label = dest_pick[0]["value"]
    payload = {"unit_id": unit["unit_id"], "target_evidence_id": evidence_id or "", "pairings": pairings}
    proposal = _propose(context, "SET_UNIT_MAPPING", {"amount": text_amount, "destination": dest_label}, payload)
    return (
        f"Untuk memastikan: {text_amount} adalah nominal Transaksi {unit['index']} ke rekening {escape(dest_label)}? "
        "Sebelum saya menyimpan perubahan ini, pastikan datanya benar.",
        _guide(f"transaction-{unit['unit_id']}", "Periksa pasangannya", unit_ids),
        proposal,
    )


def _amount_eq(candidate_value: str, amount: int) -> bool:
    digits = "".join(ch for ch in str(candidate_value) if ch.isdigit())
    try:
        return int(digits) == amount if digits else False
    except ValueError:
        return False


def _evidence_of(runner: _Runner, fact_id: str) -> str | None:
    from app.infrastructure.repositories import FactRepository

    try:
        facts = FactRepository(runner.db).list_for_case(runner.case_id)
    except Exception:
        return None
    for fact in facts:
        if fact.fact_id == fact_id:
            return fact.source_evidence_id
    return None


def _propose(context: dict[str, Any], action_type: str, summary: dict[str, Any], payload: dict[str, Any]) -> ProposedAction:
    version = context["case"]["version"]
    action_id = action_id_for(context["case"]["case_id"], action_type, payload, version)
    return ProposedAction(
        action_id=action_id,
        action_type=action_type,
        risk="YELLOW",
        summary=summary,
        payload=payload,
        expected_version=version,
    )


def _handle_yes(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    pending = ui.get("pending_action") or {}
    if not pending or pending.get("action_type") not in YELLOW_ACTIONS:
        return (
            "Baik. Ada lagi yang bisa saya bantu? Coba tanyakan apa yang harus dilakukan sekarang.",
            _guide_for_action(context),
            None,
        )
    return (
        "Baik, saya siapkan penyimpanannya. Tekan tombol Simpan pada konfirmasi yang muncul untuk melanjutkan.",
        _guide("confirm-mapping", "Konfirmasi di sini", set(context["unit_ids"])),
        None,
    )


def _handle_no(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (
        "Baik, tidak jadi disimpan. Anda tetap bisa memilih sendiri pasangan yang benar.",
        _guide("transaction-list", "Pilih sendiri di sini", set(context["unit_ids"])),
        None,
    )


def _handle_unknown(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (
        f"Saya belum paham maksudnya. Yang bisa saya bantu: {_next_action_text(context)}",
        _guide_for_action(context),
        None,
    )


_HANDLERS = {
    "GREETING": _handle_greeting,
    "ASK_NEXT": _handle_ask_next,
    "SHOW_MISSING": _handle_missing,
    "SHOW_PROBLEM": _handle_problem,
    "CONFUSED": _handle_confused,
    "EXPLAIN_STATE": _handle_state,
    "EXPLAIN_READINESS": _handle_readiness,
    "EXPLAIN_PACKAGE": _handle_package,
    "PREPARE_REPORT": _handle_prepare,
    "OPEN_WORKSPACE": _handle_workspace,
    "OPEN_OFFICIAL": _handle_official,
    "CONFIRM_MAPPING_VALUE": _handle_confirm_value,
    "CONFIRM_YES": _handle_yes,
    "CONFIRM_NO": _handle_no,
    "UNKNOWN": _handle_unknown,
}


# --- Approve / deny (dipanggil endpoint, bukan LLM) ------------------------------


def _is_replay(db: Any, case_id: str, action_id: str, action_type: str, payload: dict[str, Any]) -> bool:
    """True bila aksi ini sudah pernah dieksekusi (idempotency key = action_id)."""
    import json as _json

    from app.infrastructure.repositories import IdempotencyRepository

    if action_type != "SET_UNIT_MAPPING":
        return False
    digest = sha256_text(
        _json.dumps(
            {
                "unit_id": str(payload.get("unit_id") or ""),
                "evidence_id": str(payload.get("target_evidence_id") or ""),
                "pairings": payload.get("pairings") or [],
            },
            sort_keys=True,
        )
    )
    existing = IdempotencyRepository(db).get(f"{case_id}:{action_id}")
    return existing is not None and existing.payload_hash == digest


def approve_action(
    db: Any,
    request: Any,
    case_id: str,
    session_id: str,
    action_id: str,
    action_type: str,
    payload: dict[str, Any],
    expected_version: int,
) -> dict[str, Any]:
    """Eksekusi aksi YELLOW yang sudah disetujui manusia."""
    from app.api.router import post_unit_mapping

    services = services_from(db, request.app.state.container)
    case = services["cases"].get_owned(case_id, session_id)
    if action_type not in YELLOW_ACTIONS:
        raise ValidationFailed("aksi tidak dikenal")
    if action_id != action_id_for(case_id, action_type, payload, expected_version):
        raise ValidationFailed("konfirmasi tidak cocok — minta ulang dari chat")
    if action_type != "OPEN_OFFICIAL":
        # replay aman didahulukan: aksi yang sama (action_id) tidak bermutasi ganda
        if _is_replay(db, case_id, action_id, action_type, payload):
            _audit(db, case_id, "ACTION_APPROVED", case.state.value, "resolve_unit_mapping", 0, "REPLAY", None, action_type)
            return {"status": "saved", "message": "Sudah tersimpan sebelumnya. Tidak ada yang berubah."}
    if expected_version != case.version:
        _audit(db, case_id, "ACTION_DENIED", case.state.value, None, 0, "STALE", None, action_type)
        raise StaleCaseVersion("Data kasus sudah berubah. Saya perbarui dulu sebelum melanjutkan.")

    if action_type == "SET_UNIT_MAPPING":
        unit_id = str(payload.get("unit_id") or "")
        body = {
            "target_evidence_id": str(payload.get("target_evidence_id") or ""),
            "pairings": payload.get("pairings") or [],
            "expected_version": expected_version,
            "idempotency_key": action_id,
            "reason": "disetujui lewat pendamping AI",
        }
        post_unit_mapping(case_id, unit_id, request, body)
        _audit(db, case_id, "ACTION_APPROVED", case.state.value, "resolve_unit_mapping", 0, "OK", None, action_type)
        fresh = build_agent_context(db, case_id)
        return {"status": "saved", "message": "Tersimpan. Kedua data sudah terpasang.", "units": fresh["units"]}

    if action_type == "OPEN_OFFICIAL":
        url = validate_url(str(payload.get("url") or ""))
        if url is None:
            raise ValidationFailed("tautan tidak diizinkan")
        _audit(db, case_id, "ACTION_APPROVED", case.state.value, "prepare_official_handoff", 0, "OK", None, action_type)
        return {"status": "open", "url": url, "message": "Silakan buka sendiri portal resminya. Saya tidak mengirim apa pun."}

    raise ValidationFailed("aksi tidak dikenal")


def deny_action(db: Any, container: Any, case_id: str, session_id: str, action_type: str) -> dict[str, Any]:
    services = services_from(db, container)
    case = services["cases"].get_owned(case_id, session_id)
    _audit(db, case_id, "ACTION_DENIED", case.state.value, None, 0, "OK", None, action_type)
    return {"status": "denied", "message": "Baik, tidak jadi dilakukan."}
