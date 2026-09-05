"""ConversationService: pesan → tool Tanggap60 → respons terstruktur.

Agentic flow yang causal (bukan chatbot prompt-panjang, bukan decorative tool proof):
1. RED safety pre-check (deterministik, tolak sebelum menyentuh tool/model).
2. Baca current case state (konteks terstruktur, tanpa PII mentah).
3. Hermes / Deterministic Planner:
   - Evaluasi state. Jika state tidak memiliki allowed tool (misal: NEW),
     mode LOCAL_GUIDE aktif: TANPA fake tool execution, tools_used kosong.
   - Jika state mengizinkan tool, pilih MINIMAL tool (1 tool utama) yang relevan.
   - Catat event AGENT_PLANNER_DECISION (planner, candidate_tools, selected_tools, latency_ms).
4. Eksekusi HANYA tool yang dipilih via `execute_tool()`:
   - Catat AGENT_TOOL_REQUEST & AGENT_TOOL_RESULT dengan durasi nyata.
   - Hasil tool menjadi Observation nyata.
5. Response Composer:
   - Handler membaca Observation dari tool yang baru saja dieksekusi.
   - Template tetap mengonsumsi data observasi (causal).
"""

from __future__ import annotations

import time
from typing import Any, cast

from app.agent.broker import (
    YELLOW_ACTIONS,
    ProposedAction,
    action_id_for,
    build_plan,
    canonical_page_for,
    red_message,
    validate_guide_target,
    validate_url,
)
from app.agent.context import build_agent_context
from app.agent.formatting import escape, format_rupiah, mask_account, redact
from app.agent.intents import Intent, classify
from app.agent.native_actions import validate_native_action
from app.agent.workspace import prepare_workspace
from app.config import TOOL_VERSION
from app.deps import services_from
from app.domain.errors import StaleCaseVersion, ValidationFailed
from app.domain.models import AuditEventRecord
from app.domain.policies import sha256_text
from app.hermes.tool_registry import ToolContext, execute_tool
from app.hermes.tools.catalog import allowed_tools
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import EventRepository
from app.services.cases import now_utc
from app.services.ids import new_id

# Tool candidate preferences per intent (diurutkan dari yang paling primer)
_INTENT_CANDIDATE_TOOLS: dict[str, tuple[str, ...]] = {
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
    "ASSIST_FULL": ("recommend_next_action", "compile_reporting_units"),
    "OPEN_TX": ("compile_reporting_units",),
    "SHOW_EVIDENCE": ("compile_reporting_units",),
    "ASK_NEEDED_EVIDENCE": (),
    "EXPLAIN_UPLOAD": (),
    # Kontrol lokal murni (pause/resume/stop/voice-decide): konteks sudah
    # dibangun di handle_message; tanpa eksekusi tool tambahan.
    "CONFIRM_YES": (),
    "CONFIRM_NO": (),
    "PAUSE": (),
    "RESUME": (),
    "STOP_ALL": (),
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
    """Eksekutor causal: memilih minimal tool set dan mengembalikan observation."""

    def __init__(self, db: Any, container: Any, case_id: str, state: str) -> None:
        self.db = db
        self.container = container
        self.case_id = case_id
        self.state = state
        self.allowed = set(allowed_tools(state))
        self.used: list[dict[str, Any]] = []
        self.observations: dict[str, Any] = {}
        self.tool_ms = 0
        services = services_from(db, container)
        self.tool_ctx = cast(ToolContext, services["ctx"])

    def plan_and_execute(self, intent_kind: str) -> tuple[str, list[str]]:
        """Pilih minimal tool yang diizinkan, lalu eksekusi untuk mendapatkan observation."""
        candidates = _INTENT_CANDIDATE_TOOLS.get(intent_kind, _INTENT_CANDIDATE_TOOLS["UNKNOWN"])
        valid_options = [c for c in candidates if c in self.allowed]

        hermes = self.container.hermes
        is_hermes_cli = bool(getattr(hermes, "hermes_cli_configured", False))

        if not valid_options:
            # State tidak punya allowed tools (misalnya NEW/COMPLETE/FAILED_SAFE)
            # Jujur: tidak ada tool yang dieksekusi, tidak ada Hermes decision
            _audit(
                self.db,
                self.case_id,
                "AGENT_PLANNER_DECISION",
                self.state,
                tool_name=None,
                duration_ms=0,
                result_code="LOCAL_GUIDE",
                summary=f"mode=LOCAL_GUIDE;state={self.state};no_tools_allowed",
                planner="LOCAL_CONTEXT",
            )
            return ("LOCAL_GUIDE", [])

        # Hermes memilih jika tersedia, jika tidak deterministik ambil first candidate yang valid
        t0 = time.perf_counter()
        selected_tool: str | None = None
        planner_mode = "DETERMINISTIC_SAFE"
        if is_hermes_cli:
            try:
                proposed = hermes.propose_tool(self.state, {"allowed_tools": valid_options, "agent": True})
                if proposed in valid_options:
                    selected_tool = proposed
                    planner_mode = "HERMES_CLI"
            except Exception:
                selected_tool = None

        if selected_tool is None:
            selected_tool = valid_options[0]
            planner_mode = "DETERMINISTIC_SAFE"

        plan_latency = int((time.perf_counter() - t0) * 1000)

        # Audit event planner decision yang jujur dan dapat diaudit
        _audit(
            self.db,
            self.case_id,
            "AGENT_PLANNER_DECISION",
            self.state,
            tool_name=selected_tool,
            duration_ms=plan_latency,
            result_code="OK",
            summary=f"planner={planner_mode};candidates={','.join(valid_options)};selected={selected_tool}",
            planner=planner_mode,
        )

        # Causal execution: hanya jalankan tool yang terpilih
        self._execute_single(selected_tool, planner_mode)
        return (planner_mode, [selected_tool])

    def _execute_single(self, name: str, planner: str) -> None:
        start = time.perf_counter()
        _audit(self.db, self.case_id, "AGENT_TOOL_REQUEST", self.state, name, None, "OK", None, "", planner)
        obs = execute_tool(name, self._state_obj(), {"case_id": self.case_id}, self.tool_ctx)
        duration = int((time.perf_counter() - start) * 1000)
        self.tool_ms += duration
        self.observations[name] = obs
        self.used.append({"tool": name, "planner": planner, "duration_ms": duration})
        _audit(self.db, self.case_id, "AGENT_TOOL_RESULT", self.state, name, duration, "OK", None, "", planner)

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
    request: Any = None,
) -> dict[str, Any]:
    """Titik masuk percakapan. Kembalikan respons terstruktur (JSON)."""
    started = time.perf_counter()
    services = services_from(db, container)
    services["cases"].get_owned(case_id, session_id)
    ui_state = {**(ui_state or {}), "raw_text": text, "_session": session_id, "_request": request}
    voice = bool((ui_state or {}).get("voice"))

    intent = classify(text)
    if intent.kind == "RED":
        message = red_message(intent.red_category)
        context = build_agent_context(db, case_id)
        _audit(db, case_id, "SENSITIVE_STOP", context["case"]["state"], None, 0, "DENIED", intent.red_category, text)
        return _response(context, message, None, None, None, [], started, 0, "DETERMINISTIC_SAFE", False)

    context = build_agent_context(db, case_id)
    state = context["case"]["state"]
    _audit(db, case_id, "AGENT_MESSAGE", state, None, 0, "OK", None, text)
    if voice:
        _audit(db, case_id, "VOICE_COMMAND", state, None, 0, "OK", None, intent.kind)

    runner = _Runner(db, container, case_id, state)
    planner_mode, _ = runner.plan_and_execute(intent.kind)

    handler = _HANDLERS.get(intent.kind, _handle_unknown)
    message, guidance, proposal = handler(intent, context, runner, ui_state)

    if proposal is not None:
        _audit(
            db,
            case_id,
            "ACTION_PROPOSED",
            state,
            None,
            0,
            "OK",
            None,
            f"{proposal.action_type} {proposal.action_id}",
        )
    if guidance is not None:
        _audit(db, case_id, "GUIDANCE_SHOWN", state, None, 0, "OK", None, guidance["target"])
    inline = ui_state.pop("_inline_plan", None)
    plan = inline if isinstance(inline, list) else _plan_for(guidance, context, ui_state)
    if plan:
        _audit(
            db,
            case_id,
            "GUIDANCE_PLAN",
            state,
            None,
            0,
            "OK",
            None,
            f"{len(plan)} steps -> {guidance['target'] if guidance else ''}",
        )

    hermes_configured = bool(getattr(container.hermes, "hermes_cli_configured", False))
    body = _response(
        context,
        message,
        guidance,
        plan,
        proposal,
        runner.used,
        started,
        runner.tool_ms,
        planner_mode,
        hermes_configured,
    )
    body.update(_take_control(ui_state))
    return body


def _response(
    context: dict[str, Any],
    message: str,
    guidance: dict[str, str] | None,
    plan: list[dict[str, Any]] | None,
    proposal: ProposedAction | None,
    tools_used: list[dict[str, Any]],
    started: float,
    tool_ms: int,
    planner_mode: str,
    hermes_configured: bool,
) -> dict[str, Any]:
    total_ms = int((time.perf_counter() - started) * 1000)

    if not tools_used:
        fallback_note = "Mode panduan lokal (LOCAL_GUIDE); status kasus tidak memerlukan eksekusi tool."
    elif hermes_configured:
        fallback_note = f"Hermes planner aktif ({planner_mode}); mengeksekusi minimal tool set."
    else:
        fallback_note = "Hermes CLI/endpoint tidak terkonfigurasi; router deterministik + minimal tool Tanggap60."

    body: dict[str, Any] = {
        "message": message,
        "quick_actions": context["quick_actions"],
        "guidance": guidance,
        "guidance_plan": plan,
        "proposed_action": None,
        "tools_used": tools_used,
        "agent_response_ms": total_ms,
        "agent_tool_ms": tool_ms,
        "state": context["case"]["state"],
        "case_version": context["case"]["version"],
        "technical": {
            "planner_modes": [planner_mode] if tools_used else ["LOCAL_GUIDE"],
            "fallback_note": fallback_note,
        },
    }
    if proposal is not None:
        body["proposed_action"] = {
            "action_id": proposal.action_id,
            "action_type": proposal.action_type,
            "risk": proposal.risk,
            "summary": proposal.summary,
            "payload": proposal.payload,
            "expected_version": proposal.expected_version,
        }
        body["quick_actions"] = []
    return body


def _take_control(ui: dict[str, Any]) -> dict[str, Any]:
    """Ambil flag kontrol sekali-pakai dari handler (voice_note/pause/stop/rollback/open_url)."""
    control = ui.pop("_control", None)
    base = {"voice_note": None, "pause_agent": False, "stop_agent": False, "rollback_drafts": False,
            "draft_committed": False, "open_url": None}
    if isinstance(control, dict):
        for key in base:
            if key in control:
                base[key] = control[key]
    return base


def _native_steps_for_set_draft(unit_id: str, field: str, fact_id: str, label: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Langkah SET_DRAFT yang lolos validasi kandidat penuh; kosong bila invalid."""
    valid = validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": unit_id, "field": field, "fact_id": fact_id, "label": label},
        context,
    )
    if valid is None:
        return []
    return [{"type": "SET_DRAFT", "unit": unit_id, "field": field, "fact_id": fact_id, "label": label}]


def _open_units(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [u for u in context["units"] if u.get("mapping_status") in {"AMBIGUOUS", "INCOMPLETE"}]


def _nav_steps(page: str, ui: dict[str, Any]) -> list[dict[str, Any]]:
    current = str((ui or {}).get("current_page") or "")
    return [{"type": "NAVIGATE_INTERNAL", "route": page}] if page and page != current else []


def _tx_action_plan(
    unit: dict[str, Any],
    context: dict[str, Any],
    ui: dict[str, Any],
    *,
    status_text: str,
    callout_title: str,
    callout_text: str,
    prefill: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Plan aksi native untuk satu transaksi: STATUS → buka → sorot → tunjuk → [draf] → CALLOUT → WAIT."""
    uid = unit["unit_id"]
    unit_ids = set(context["unit_ids"])
    steps: list[dict[str, Any]] = [
        {"type": "STATUS", "message": status_text},
        *_nav_steps("review", ui),
        {"type": "OPEN_TRANSACTION", "target": f"transaction-{uid}"},
        {"type": "SPOTLIGHT", "target": f"transaction-{uid}"},
        {"type": "MOVE_POINTER", "target": f"transaction-{uid}-amount"},
    ]
    if prefill:
        steps.extend(_native_steps_for_set_draft(uid, prefill["field"], prefill["fact_id"], prefill["label"], context))
    steps.extend(
        [
            {"type": "CALLOUT", "target": f"transaction-{uid}-amount", "title": callout_title, "message": callout_text},
            {"type": "WAIT_FOR_USER"},
        ]
    )
    return build_plan(steps, unit_ids)


def _pick_unit(context: dict[str, Any], ordinal: int | None) -> dict[str, Any] | None:
    """Pilih unit: ordinal 1-based (index tampilan), -1 = terakhir, 0/None = terbuka pertama."""
    ordered = sorted(context["units"], key=lambda u: (u.get("index", 0), u.get("unit_id", "")))
    if not ordered:
        return None
    if ordinal is not None and ordinal >= 1:
        return ordered[ordinal - 1] if ordinal <= len(ordered) else None
    if ordinal == -1:
        return ordered[-1]
    for unit in ordered:
        if unit.get("mapping_status") in {"AMBIGUOUS", "INCOMPLETE"}:
            return unit
    return ordered[0]


def _followup_plan(
    guide: dict[str, str] | None,
    fresh: dict[str, Any],
    ui: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rencana berikut setelah commit: buka target next-action secara native.

    Agentic loop §26 — observe (fresh) → plan → execute. Target transaksi
    mendapat alur ACT penuh; target lain mendapat alur visual generik.
    """
    if not guide:
        return []
    target = str(guide.get("target") or "")
    unit_ids = set(fresh["unit_ids"])
    if target.startswith("transaction-"):
        uid = target[len("transaction-"):].split("-")[0]
        unit = next((u for u in fresh.get("units", []) if u.get("unit_id") == uid), None)
        if unit is not None and unit.get("mapping_status") in {"AMBIGUOUS", "INCOMPLETE"}:
            return _tx_action_plan(
                unit, fresh, ui,
                status_text="Tersimpan. Lanjut ke transaksi berikut.",
                callout_title="Pastikan nominal",
                callout_text="Pilih pasangan jumlah, rekening, dan waktu yang benar. Saya tidak akan menebak.",
            )
    if target == "next-best-action":
        return build_plan(
            [
                {"type": "STATUS", "message": "Tersimpan. Saya menunjukkan tindakan paling penting."},
                *_nav_steps("readiness", ui),
                {"type": "SCROLL_TO", "target": "next-best-action"},
                {"type": "SPOTLIGHT", "target": "next-best-action"},
                {"type": "MOVE_POINTER", "target": "next-best-action"},
                {
                    "type": "CALLOUT", "target": "next-best-action", "title": "Lakukan ini dulu",
                    "message": "Ini langkah paling penting sekarang. Ikuti bagian yang disorot.",
                },
                {"type": "WAIT_FOR_USER"},
            ],
            unit_ids,
        )
    page = canonical_page_for(target)
    steps: list[dict[str, Any]] = [{"type": "STATUS", "message": "Tersimpan. Lanjut ke langkah berikut."}]
    if page:
        steps.extend(_nav_steps(page, ui))
    steps.extend(
        [
            {"type": "SCROLL_TO", "target": target},
            {"type": "SPOTLIGHT", "target": target},
            {"type": "WAIT_FOR_USER"},
        ]
    )
    return build_plan(steps, unit_ids)


def _guide(target: str | None, label: str, unit_ids: set[str]) -> dict[str, str] | None:
    if not target:
        return None
    valid = validate_guide_target(target, unit_ids)
    if valid is None:
        return None
    return {"target": valid, "label": label}


def _plan_for(
    guidance: dict[str, str] | None,
    context: dict[str, Any],
    ui_state: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Derivasi guidance_plan deterministik untuk 3 flow hero (Live Rescue fase 1).

    - review-facts + konflik BLOCKING terbuka → plan konflik nominal.
    - transaction-<uid> + unit AMBIGUOUS → plan pairing transaksi.
    - next-best-action → plan tindakan utama.
    Kembalikan None bila flow tidak cocok (frontend memakai one-shot legacy).
    Semua teks ditulis di kode (bukan output model); target & route tervalidasi.
    """
    if guidance is None:
        return None
    target = guidance.get("target", "")
    unit_ids = set(context.get("unit_ids", []))
    current = str((ui_state or {}).get("current_page") or "")
    page = canonical_page_for(target)

    nav: list[dict[str, Any]] = []
    if page and page != current:
        nav.append({"type": "NAVIGATE_INTERNAL", "route": page})

    # conflicts_open hanya berisi konflik terbuka (tanpa field status).
    blocking = [c for c in context.get("conflicts_open", []) if c.get("severity") == "BLOCKING"]
    if target == "review-facts" and blocking:
        return build_plan(
            [
                {"type": "STATUS", "message": "Saya membuka data yang bertentangan."},
                *nav,
                {"type": "SCROLL_TO", "target": "review-facts"},
                {"type": "SPOTLIGHT", "target": "review-facts"},
                {
                    "type": "CALLOUT",
                    "target": "review-facts",
                    "title": "Pilih yang benar",
                    "message": "Saya menemukan data yang berbeda. Saya tidak akan menebak — pilih yang sesuai bukti.",
                },
                {"type": "WAIT_FOR_USER"},
            ],
            unit_ids,
        )
    if target != "transaction-list" and target.startswith("transaction-"):
        uid = target[len("transaction-") :].split("-")[0]
        unit = next((u for u in context.get("units", []) if u.get("unit_id") == uid), None)
        if unit is not None and unit.get("mapping_status") == "AMBIGUOUS" and uid in unit_ids:
            return build_plan(
                [
                    {"type": "STATUS", "message": "Saya membuka transaksi yang perlu dipasangkan."},
                    *nav,
                    {"type": "SCROLL_TO", "target": f"transaction-{uid}"},
                    {"type": "SPOTLIGHT", "target": f"transaction-{uid}"},
                    {"type": "MOVE_POINTER", "target": f"transaction-{uid}-amount"},
                    {
                        "type": "CALLOUT",
                        "target": f"transaction-{uid}-amount",
                        "title": "Pastikan nominal",
                        "message": "Pilih pasangan jumlah, rekening, dan waktu yang benar. Saya tidak akan menebak.",
                    },
                    {"type": "WAIT_FOR_USER"},
                ],
                unit_ids,
            )
    if target == "next-best-action":
        return build_plan(
            [
                {"type": "STATUS", "message": "Saya menunjukkan tindakan paling penting."},
                *nav,
                {"type": "SCROLL_TO", "target": "next-best-action"},
                {"type": "SPOTLIGHT", "target": "next-best-action"},
                {"type": "MOVE_POINTER", "target": "next-best-action"},
                {
                    "type": "CALLOUT",
                    "target": "next-best-action",
                    "title": "Lakukan ini dulu",
                    "message": "Ini langkah paling penting sekarang. Ikuti bagian yang disorot.",
                },
                {"type": "WAIT_FOR_USER"},
            ],
            unit_ids,
        )
    return None


def _unit_label(unit: dict[str, Any]) -> str:
    amount = unit.get("amount_text") or ""
    dest = unit.get("destination_masked") or ""
    bits = []
    if amount and amount != "—":
        bits.append(amount)
    if dest:
        bits.append(f"ke {dest}")
    if bits:
        return f"Transaksi {unit.get('index', '')} ({' '.join(bits)})".replace("  ", " ").strip()
    return f"Transaksi {unit.get('index', '')}".strip()


# --- Handlers per intent yang mengonsumsi Observation nyata dari runner ---


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
        _next_action_text(context, runner.observations.get("recommend_next_action")),
        _guide_for_action(context, runner.observations.get("recommend_next_action")),
        None,
    )


def _next_action_text(context: dict[str, Any], observation: dict[str, Any] | None = None) -> str:
    # Causal consumption: utamakan hasil observation yang baru dijalankan tool recommend_next_action
    action = observation or context["next_action"]
    code = action.get("code")
    target = action.get("target_unit_id")
    unit = next((u for u in context["units"] if u["unit_id"] == target), None)
    who = f" {_unit_label(unit)}." if unit else "."
    mapping = {
        "CONTACT_BANK_PJP": f"Ada transaksi yang banknya sudah siap{who} Hubungi bank lewat situs resmi sekarang. Tidak perlu menunggu transaksi lain.",
        "PREPARE_IASC_UNIT": f"Ada transaksi yang siap dilaporkan{who} Siapkan datanya, lalu buka portal resmi IASC.",
        "RESOLVE_CONFLICT": "Ada data yang saling bertentangan. Pilih yang benar supaya paketnya akurat — saya tandai bagian itu.",
        "RESOLVE_UNIT_MAPPING": f"Ada transaksi yang belum terpasang{who} Pilih pasangan jumlah uang, rekening, dan waktu yang benar.",
        "CONFIRM_TRANSACTION_AMOUNT": f"Jumlah uang {who} belum jelas. Konfirmasi nominal yang sesuai bukti.",
        "CONFIRM_TRANSACTION_TIME": f"Waktu transfer {who} belum jelas. Konfirmasi waktunya.",
        "CONFIRM_DESTINATION": f"Rekening tujuan {who} belum jelas. Konfirmasi rekeningnya.",
        "ADD_TRANSFER_EVIDENCE": "Tambah bukti transfer yang memuat jumlah uang, rekening, dan waktu.",
        "PREPARE_POLICE_INCIDENT": "Setelah urusan bank, siapkan kronologi untuk situs resmi kepolisian.",
        "APPROVE_READY_UNIT": "Ada transaksi menunggu persetujuan Anda untuk dibuatkan paket terverifikasi.",
        "DOWNLOAD_VERIFIED_PACK": "Semua unit sudah diproses. Unduh paket terverifikasi lalu lakukan handoff manual.",
        "OPEN_IASC_HANDOFF": "Buka portal resmi IASC dan isi datanya sendiri. Saya sudah menyiapkan ringkasannya.",
        "RECORD_RECEIPT": "Catat nomor laporan resmi bila sudah ada.",
    }
    return mapping.get(str(code or ""), action.get("reason") or "Mari periksa kondisi kasus Anda langkah demi langkah.")


def _guide_for_action(context: dict[str, Any], observation: dict[str, Any] | None = None) -> dict[str, str] | None:
    unit_ids = set(context["unit_ids"])
    action = observation or context["next_action"]
    code = action.get("code")
    target = action.get("target_unit_id")
    if target and validate_guide_target(f"transaction-{target}", unit_ids):
        return {"target": f"transaction-{target}", "label": "Lihat transaksi ini"}
    mapping = {
        "RESOLVE_CONFLICT": ("review-facts", "Selesaikan di sini"),
        "RESOLVE_UNIT_MAPPING": ("confirm-mapping", "Pasangkan di sini"),
        "APPROVE_READY_UNIT": ("approve-package", "Buat paket di sini"),
        "DOWNLOAD_VERIFIED_PACK": ("approve-package", "Unduh di sini"),
        "OPEN_IASC_HANDOFF": ("official-handoff", "Buka portal resmi"),
        "ADD_TRANSFER_EVIDENCE": ("upload-evidence", "Tambah bukti di sini"),
    }
    if code in mapping:
        target_name, label = mapping[code]
        return _guide(target_name, label, unit_ids)
    return _guide("next-best-action", "Lihat tindakan utama", unit_ids)


def _handle_ask_next(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    obs = runner.observations.get("recommend_next_action")
    return (_next_action_text(context, obs), _guide_for_action(context, obs), None)


def _handle_needed_evidence(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    return (
        "Foto transfer, cuplikan chat, atau link toko. Yang memuat jumlah uang, rekening tujuan, dan waktu paling berguna. Jangan kirim password, OTP, atau KTP.",
        _guide("upload-evidence", "Kirim bukti di sini", unit_ids),
        None,
    )


def _handle_explain_upload(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    return (
        "Ketuk kotak unggah, pilih foto JPG/PNG atau PDF. Maksimal 8 file, total 25 MB. Setelah itu tekan Periksa bukti.",
        _guide("upload-evidence", "Unggah di sini", unit_ids),
        None,
    )


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
    obs = runner.observations.get("recommend_next_action")
    return (
        f"Tidak apa-apa, saya bantu. {_next_action_text(context, obs)} Saya tandai yang perlu diperbaiki.",
        _guide_for_action(context, obs),
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
    obs = runner.observations.get("assess_handoff_readiness")
    overall = obs.get("overall_status") if obs else context["readiness_overall"]
    if overall == "READY":
        return (
            "Semuanya siap. Tinggal persetujuan Anda untuk dibuatkan paket.",
            _guide("approve-package", "Buat paket di sini", set(context["unit_ids"])),
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
    obs = runner.observations.get("prepare_official_handoff")
    configured = (obs.get("official_url") if obs else "") or str(
        getattr(runner.container.settings, "official_iasc_url", "") or ""
    )
    url = validate_url(configured) or "https://iasc.ojk.go.id/"
    proposal = _propose(context, "OPEN_OFFICIAL", {"url": url}, {"url": url}, runner)
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
    proposal = _propose(context, "SET_UNIT_MAPPING", {"amount": text_amount, "destination": dest_label}, payload, runner)
    # Native Action Mode: sertakan prefill draf (PREPARE, bukan commit).
    # Commit server tetap menunggu approval eksplisit (tombol / voice "iya").
    ui["_inline_plan"] = _tx_action_plan(
        unit,
        context,
        ui,
        status_text="Saya menyiapkan pilihannya.",
        callout_title="Periksa draf AI",
        callout_text=f"Saya memilih {text_amount} sebagai draf. Belum disimpan — pastikan dulu.",
        prefill={"field": "amount", "fact_id": amount_fact["fact_id"], "label": text_amount},
    )
    if any(s.get("type") == "SET_DRAFT" for s in ui["_inline_plan"]):
        _audit(
            runner.db, context["case"]["case_id"], "DRAFT_PREPARED",
            context["case"]["state"], None, 0, "OK", None,
            f"SET_DRAFT {unit['unit_id']} amount {amount_fact['fact_id']}",
        )
        ui["_control"] = {
            "voice_note": f"{text_amount} ke {mask_account(dest_label)}. Benar?",
        }
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


def _propose(
    context: dict[str, Any], action_type: str, summary: dict[str, Any], payload: dict[str, Any], runner: _Runner | None = None
) -> ProposedAction:
    version = context["case"]["version"]
    secret = str(getattr(getattr(runner, "container", None), "settings", None).secret_key) if runner and hasattr(runner, "container") else None
    action_id = action_id_for(context["case"]["case_id"], action_type, payload, version, secret_key=secret)
    return ProposedAction(
        action_id=action_id,
        action_type=action_type,
        risk="YELLOW",
        summary=summary,
        payload=payload,
        expected_version=version,
    )


def _pending_yellow(ui: dict[str, Any]) -> dict[str, Any] | None:
    pending = ui.get("pending_action") or {}
    if isinstance(pending, dict) and pending.get("action_type") in YELLOW_ACTIONS:
        return pending
    return None


def _handle_yes(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    pending = _pending_yellow(ui)
    if not pending:
        return (
            "Baik. Ada lagi yang bisa saya bantu? Coba tanyakan apa yang harus dilakukan sekarang.",
            _guide_for_action(context),
            None,
        )
    # Voice approval (§14): "iya/benar/simpan" via suara = approval eksplisit
    # bila proposal aktif, jelas, Yellow, tak sensitif, belum expired (versi cocok).
    if ui.get("voice") and ui.get("_request") is not None:
        from app.domain.errors import StaleCaseVersion, ValidationFailed

        try:
            result = approve_action(
                runner.db,
                ui["_request"],
                context["case"]["case_id"],
                str(ui.get("_session") or ""),
                str(pending.get("action_id") or ""),
                str(pending.get("action_type") or ""),
                dict(pending.get("payload") or {}),
                int(pending.get("expected_version") or 0),
            )
        except StaleCaseVersion:
            return (
                "Data kasus sudah berubah. Saya perbarui dulu sebelum melanjutkan.",
                _guide("review-facts", "Periksa lagi di sini", set(context["unit_ids"])),
                None,
            )
        except (ValidationFailed, ValueError, TypeError) as exc:
            return (
                f"Konfirmasi tidak bisa dipakai ({exc}). Minta saya siapkan ulang.",
                _guide("transaction-list", "Pilih di sini", set(context["unit_ids"])),
                None,
            )
        _audit(
            runner.db, context["case"]["case_id"], "VOICE_APPROVAL",
            context["case"]["state"], None, 0, "OK", None,
            str(pending.get("action_type") or ""),
        )
        if isinstance(result, dict) and result.get("url"):
            ui["_control"] = {"open_url": result["url"]}
            return (result.get("message") or "Silakan buka sendiri portal resminya.", None, None)
        # Agentic loop (§26): setelah commit, baca ulang state → rencana berikut.
        fresh = build_agent_context(runner.db, context["case"]["case_id"])
        nxt = _guide_for_action(fresh, None)
        ui["_inline_plan"] = _followup_plan(nxt, fresh, ui)
        ui["_control"] = {"voice_note": "Transaksi disimpan.", "draft_committed": True}
        saved_msg = result.get("message") if isinstance(result, dict) else None
        return (
            f"{saved_msg or 'Tersimpan.'} {_next_action_text(fresh, None)}",
            nxt,
            None,
        )
    return (
        "Baik, saya siapkan penyimpanannya. Tekan tombol Simpan pada konfirmasi yang muncul untuk melanjutkan.",
        _guide("confirm-mapping", "Konfirmasi di sini", set(context["unit_ids"])),
        None,
    )


def _handle_no(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    if _pending_yellow(ui):
        try:
            deny_action(
                runner.db, runner.container, context["case"]["case_id"],
                str(ui.get("_session") or ""), str((ui.get("pending_action") or {}).get("action_type") or ""),
            )
        except Exception:
            pass
        ui["_control"] = {"rollback_drafts": True}
    return (
        "Baik, tidak jadi disimpan. Anda tetap bisa memilih sendiri pasangan yang benar.",
        _guide("transaction-list", "Pilih sendiri di sini", set(context["unit_ids"])),
        None,
    )


def _handle_assist(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    """"Bantu saya sampai selesai": observe state → plan aksi aman berikut (§11, §36)."""
    unit_ids = set(context["unit_ids"])
    blocking = [c for c in context["conflicts_open"] if c.get("severity") == "BLOCKING"]
    if blocking:
        ui["_inline_plan"] = build_plan(
            [
                {"type": "STATUS", "message": "Baik. Saya periksa kasus Anda."},
                *_nav_steps("review", ui),
                {"type": "SCROLL_TO", "target": "review-facts"},
                {"type": "SPOTLIGHT", "target": "review-facts"},
                {
                    "type": "CALLOUT", "target": "review-facts", "title": "Pilih yang benar",
                    "message": "Saya menemukan data yang berbeda. Saya tidak akan menebak — pilih yang sesuai bukti.",
                },
                {"type": "WAIT_FOR_USER"},
            ],
            unit_ids,
        )
        return (
            "Baik. Saya periksa kasus Anda. Saya menemukan data yang saling bertentangan — pilih yang benar dulu.",
            _guide("review-facts", "Selesaikan di sini", unit_ids),
            None,
        )
    open_units = _open_units(context)
    if open_units:
        unit = open_units[0]
        ui["_inline_plan"] = _tx_action_plan(
            unit, context, ui,
            status_text="Baik. Saya periksa kasus Anda.",
            callout_title="Pastikan nominal",
            callout_text="Pilih pasangan jumlah, rekening, dan waktu yang benar. Saya tidak akan menebak.",
        )
        ui["_control"] = {"voice_note": "Saya membuka transaksi yang perlu diperiksa."}
        return (
            f"Baik. Saya periksa kasus Anda. {_unit_label(unit)} perlu dikonfirmasi — saya bukakan sekarang.",
            _guide(f"transaction-{unit['unit_id']}", "Periksa transaksi ini", unit_ids),
            None,
        )
    if not context["units"]:
        ui["_inline_plan"] = build_plan(
            [
                {"type": "STATUS", "message": "Baik. Saya siapkan dari awal."},
                *_nav_steps("intake", ui),
                {"type": "SCROLL_TO", "target": "upload-evidence"},
                {"type": "SPOTLIGHT", "target": "upload-evidence"},
                {
                    "type": "CALLOUT", "target": "upload-evidence", "title": "Kirim bukti dulu",
                    "message": "Foto transfer, chat, atau link — saya susun setelah bukti masuk.",
                },
                {"type": "WAIT_FOR_USER"},
            ],
            unit_ids,
        )
        return (
            "Baik. Kirim bukti yang ada dulu — foto transfer, chat, atau link. Saya susun setelah bukti masuk.",
            _guide("upload-evidence", "Kirim bukti di sini", unit_ids),
            None,
        )
    obs = runner.observations.get("recommend_next_action")
    return (
        f"Baik. {_next_action_text(context, obs)}",
        _guide_for_action(context, obs),
        None,
    )


def _handle_open_tx(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    """Buka transaksi native: ordinal / belum selesai / pertama terbuka."""
    unit_ids = set(context["unit_ids"])
    ordinal = intent.extra.get("ordinal") if isinstance(intent.extra, dict) else None
    unit = _pick_unit(context, ordinal if isinstance(ordinal, int) else None)
    if unit is None:
        return (
            "Belum ada transaksi. Kirim bukti transfer yang memuat jumlah uang, rekening, dan waktu.",
            _guide("upload-evidence", "Kirim bukti di sini", unit_ids),
            None,
        )
    ui["_inline_plan"] = _tx_action_plan(
        unit, context, ui,
        status_text="Membuka transaksi yang perlu diperiksa…",
        callout_title=_unit_label(unit),
        callout_text="Transaksi sudah terbuka. Sebutkan nominalnya bila perlu dipastikan.",
    )
    ui["_control"] = {"voice_note": "Transaksi sudah terbuka."}
    return (
        f"{_unit_label(unit)} saya bukakan. Sebutkan nominalnya bila perlu dipastikan.",
        _guide(f"transaction-{unit['unit_id']}", "Lihat transaksi ini", unit_ids),
        None,
    )


def _handle_evidence(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    unit_ids = set(context["unit_ids"])
    target = "evidence-list" if context["evidence_count"] else "upload-evidence"
    ui["_inline_plan"] = build_plan(
        [
            {"type": "STATUS", "message": "Saya membuka buktinya."},
            *_nav_steps("intake", ui),
            {"type": "OPEN_EVIDENCE", "target": target},
            {"type": "SCROLL_TO", "target": target},
            {"type": "SPOTLIGHT", "target": target},
            {
                "type": "CALLOUT", "target": target, "title": "Bukti Anda",
                "message": "Ini bukti yang sudah masuk untuk kasus ini.",
            },
            {"type": "WAIT_FOR_USER"},
        ],
        unit_ids,
    )
    return (
        "Saya membuka buktinya.",
        _guide(target, "Lihat bukti di sini", unit_ids),
        None,
    )


def _handle_pause(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    ui["_control"] = {"pause_agent": True}
    return (
        "Baik, saya jeda di sini. Ucapkan “Lanjut” kalau siap melanjutkan.",
        None,
        None,
    )


def _handle_stop(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    if _pending_yellow(ui):
        try:
            deny_action(
                runner.db, runner.container, context["case"]["case_id"],
                str(ui.get("_session") or ""), str((ui.get("pending_action") or {}).get("action_type") or ""),
            )
        except Exception:
            pass
    ui["_control"] = {"stop_agent": True, "rollback_drafts": True}
    return (
        "Baik, saya berhenti. Draf yang saya siapkan dibatalkan; yang sudah Anda simpan tetap aman.",
        None,
        None,
    )


def _handle_resume(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    """Lanjut: teruskan loop TANPA meng-approve proposal (§35)."""
    pending = _pending_yellow(ui)
    if pending:
        version = context["case"]["version"]
        try:
            want_version = int(pending.get("expected_version") or 0)
        except (TypeError, ValueError):
            want_version = -1
        if want_version != version:
            return (
                "Data kasus sudah berubah. Saya perbarui dulu sebelum melanjutkan.",
                _guide("review-facts", "Periksa lagi di sini", set(context["unit_ids"])),
                None,
            )
        secret = str(getattr(getattr(runner, "container", None), "settings", None).secret_key or "")
        expect = action_id_for(
            context["case"]["case_id"], str(pending.get("action_type") or ""),
            dict(pending.get("payload") or {}), version, secret_key=secret,
        )
        if expect != str(pending.get("action_id") or ""):
            return (
                "Konfirmasi sebelumnya tidak cocok. Minta saya siapkan ulang.",
                _guide("transaction-list", "Pilih di sini", set(context["unit_ids"])),
                None,
            )
        reproposed = _propose(
            context, str(pending.get("action_type") or ""),
            dict(pending.get("summary") or {}), dict(pending.get("payload") or {}), runner,
        )
        detail = ", ".join(f"{k}: {v}" for k, v in (reproposed.summary or {}).items())
        return (
            f"Masih menunggu keputusan Anda — {detail}. Ucapkan “Iya” untuk menyimpan, atau “Tidak” untuk membatalkan.",
            _guide("confirm-mapping", "Konfirmasi di sini", set(context["unit_ids"])),
            reproposed,
        )
    return _handle_assist(intent, context, runner, ui)


def _handle_unknown(intent: Intent, context: dict[str, Any], runner: _Runner, ui: dict[str, Any]) -> tuple:
    return (
        f"Saya belum paham maksudnya. Yang bisa saya bantu: {_next_action_text(context)}",
        _guide_for_action(context),
        None,
    )


_HANDLERS = {
    "GREETING": _handle_greeting,
    "ASK_NEXT": _handle_ask_next,
    "ASK_NEEDED_EVIDENCE": _handle_needed_evidence,
    "EXPLAIN_UPLOAD": _handle_explain_upload,
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
    "ASSIST_FULL": _handle_assist,
    "OPEN_TX": _handle_open_tx,
    "SHOW_EVIDENCE": _handle_evidence,
    "PAUSE": _handle_pause,
    "RESUME": _handle_resume,
    "STOP_ALL": _handle_stop,
    "UNKNOWN": _handle_unknown,
}


# --- Approve / deny (dipanggil endpoint, bukan LLM) ------------------------------


def _is_replay(db: Any, case_id: str, action_id: str, action_type: str, payload: dict[str, Any]) -> bool:
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
    from app.api.router import post_unit_mapping

    services = services_from(db, request.app.state.container)
    case = services["cases"].get_owned(case_id, session_id)
    if action_type not in YELLOW_ACTIONS:
        raise ValidationFailed("aksi tidak dikenal")
    secret = str(getattr(request.app.state.container.settings, "secret_key", None) or "")
    if action_id != action_id_for(case_id, action_type, payload, expected_version, secret_key=secret):
        raise ValidationFailed("konfirmasi tidak cocok — minta ulang dari chat")
    if action_type != "OPEN_OFFICIAL":
        if _is_replay(db, case_id, action_id, action_type, payload):
            _audit(
                db,
                case_id,
                "ACTION_APPROVED",
                case.state.value,
                "resolve_unit_mapping",
                0,
                "REPLAY",
                None,
                action_type,
            )
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
        _audit(
            db, case_id, "ACTION_APPROVED", case.state.value, "prepare_official_handoff", 0, "OK", None, action_type
        )
        return {
            "status": "open",
            "url": url,
            "message": "Silakan buka sendiri portal resminya. Saya tidak mengirim apa pun.",
        }

    raise ValidationFailed("aksi tidak dikenal")


def deny_action(db: Any, container: Any, case_id: str, session_id: str, action_type: str) -> dict[str, Any]:
    services = services_from(db, container)
    case = services["cases"].get_owned(case_id, session_id)
    _audit(db, case_id, "ACTION_DENIED", case.state.value, None, 0, "OK", None, action_type)
    return {"status": "denied", "message": "Baik, tidak jadi dilakukan."}
