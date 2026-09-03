from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.helpers import artifact_public, case_summary, conflict_public, evidence_public, fact_public
from app.deps import services_from
from app.domain.errors import AppError, ValidationFailed
from app.domain.states import DeclaredCondition, Mode, State
from app.infrastructure.repositories import (
    ActionRepository,
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    EvidenceRepository,
    FactRepository,
    ReceiptRepository,
    TransactionRepository,
    UnitMappingRepository,
)
from app.services.ids import new_id
from app.web.labels import human, soften

web = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["human"] = human
TEMPLATES.env.filters["soften"] = soften


def _asset_mtimes() -> list[float]:
    static = Path(__file__).parent / "static"
    return [p.stat().st_mtime for p in (static / "app.css", static / "app.js", static / "agent.css", static / "agent.js") if p.exists()]


TEMPLATES.env.globals["asset_v"] = str(int(max(_asset_mtimes(), default=0)))
ICON32 = Path(__file__).parent / "static" / "icons" / "icon-32.png"


def _sid(request: Request) -> str:
    return str(request.state.session_id)


def _svc(request: Request) -> dict:
    return services_from(request.state.db, request.app.state.container)


def _gap_texts(report: dict | None, units_report: dict | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(item: object) -> None:
        if not isinstance(item, dict):
            return
        if item.get("status") not in {"MISSING", "CONFLICT"}:
            return
        text = soften(item.get("action") or item.get("label") or "")
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    if units_report:
        for urep in units_report.get("units") or []:
            for ch in urep.get("channels") or []:
                for ck in ch.get("checks") or []:
                    add(ck)
        for ck in (units_report.get("incident_police") or {}).get("checks") or []:
            add(ck)
        return out
    if report:
        for ch in report.get("channels") or []:
            for ck in ch.get("checks") or []:
                add(ck)
    return out


def _case_units(db, case_id: str):
    from app.services.reporting_units import compile_reporting_units

    facts = FactRepository(db).list_for_case(case_id)
    evidence = EvidenceRepository(db).list_for_case(case_id)
    mappings = UnitMappingRepository(db).list_for_case(case_id)
    decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
    return compile_reporting_units(case_id, facts, evidence, decs if decs else None)


def _pairing_cards(db, case_id: str) -> list[dict]:
    units = _case_units(db, case_id)
    facts = {f.fact_id: f for f in FactRepository(db).list_for_case(case_id)}
    cards = []
    for unit in units:
        status = str(getattr(unit.mapping_status, "value", unit.mapping_status))
        if status != "AMBIGUOUS":
            continue
        dests, amounts, times = [], [], []
        for fid in unit.fact_ids:
            fact = facts.get(fid)
            if fact is None:
                continue
            kind = fact.type.value
            item = {"id": fact.fact_id, "label": fact.raw_value, "src": fact.source_evidence_id}
            if kind in {"ACCOUNT", "PJP"} and "VICTIM" not in (fact.raw_value or ""):
                dests.append(item)
            elif kind == "AMOUNT":
                amounts.append(item)
            elif kind == "DATETIME":
                times.append(item)
        evid = unit.evidence_ids[0] if unit.evidence_ids else ""
        cards.append(
            {
                "unit_id": unit.unit_id,
                "evidence_id": evid,
                "dests": dests,
                "amounts": amounts,
                "times": times,
            }
        )
    return cards


_ID_MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def _format_rupiah(value: float | int | str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        return "Rp" + f"{float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def _mask_account(raw: object) -> str:
    text = str(raw or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        prefix = text
        for chunk in sorted(set(text.split()), key=len, reverse=True):
            if chunk and any(ch.isdigit() for ch in chunk):
                prefix = text.replace(chunk, "").strip(" -•")
                break
        bank = prefix.split()[0] if prefix else "Rekening"
        return f"{bank} ••••{digits[-4:]}"
    return text


def _format_when(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return f"{dt.day} {_ID_MONTHS.get(dt.month, '')} {dt.year} · {dt.hour:02d}:{dt.minute:02d}".strip()
    except (ValueError, TypeError):
        return text


def _tx_summaries(db, case_id: str, evidence_names: dict[str, str]) -> list[dict]:
    """Relationship summaries for units that are already resolved (not AMBIGUOUS)."""
    out = []
    n = 0
    for unit in _case_units(db, case_id):
        status = str(getattr(unit.mapping_status, "value", unit.mapping_status))
        if status == "AMBIGUOUS":
            continue
        n += 1
        names = [evidence_names.get(e, "") for e in unit.evidence_ids]
        out.append(
            {
                "n": n,
                "unit_id": unit.unit_id,
                "status": status,
                "amount": _format_rupiah(unit.amount) if unit.amount else "",
                "dest": _mask_account(unit.destination_account) if unit.destination_account else "",
                "when": _format_when(unit.transferred_at),
                "src": ", ".join(dict.fromkeys(x for x in names if x)),
            }
        )
    return out


@web.get("/favicon.ico")
def favicon():
    if ICON32.exists():
        return FileResponse(ICON32, media_type="image/png")
    return RedirectResponse("/static/favicon.svg")


@web.get("/")
def home(request: Request):
    return TEMPLATES.TemplateResponse("home.html", {"request": request, "title": "SatuAman Tanggap60"})


@web.post("/start")
def start(
    request: Request,
    declared_condition: str = Form(...),
    mode: str = Form("DEMO"),
):
    case, _token = _svc(request)["cases"].create(
        mode=Mode(mode),
        condition=DeclaredCondition(declared_condition),
        session_id=_sid(request),
    )
    return RedirectResponse(f"/cases/{case.case_id}/intake", status_code=303)


@web.get("/cases/{case_id}/intake")
def intake(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    evidence = EvidenceRepository(request.state.db).list_for_case(case_id)
    return TEMPLATES.TemplateResponse(
        "intake.html",
        {
            "request": request,
            "case": case,
            "evidence": [evidence_public(e) for e in evidence],
            "can_delete_evidence": case.state in {State.NEW, State.INGESTING, State.REVIEW_REQUIRED, State.EXTRACTING},
            "summary": case_summary(request.state.db, case),
            "notice": request.query_params.get("notice", ""),
            "notice_text": request.query_params.get("notice_text", ""),
        },
    )


_FILE_ERROR_TEXT = (
    ("tipe berkas tidak diizinkan", "File itu tidak bisa dipakai. Pakai foto (JPG/PNG) atau PDF."),
    ("PDF lebih dari 20 halaman", "PDF-nya kepanjangan (maks 20 halaman). Kirim halaman yang penting saja."),
    ("gambar melebihi batas piksel", "Fotonya kegedean. Coba foto dengan resolusi lebih kecil."),
    ("maksimal 8 berkas", "Kebanyakan. Maksimal 8 file — hapus dulu yang tidak perlu."),
    ("melebihi 25 MB", "Kegedean. Total file maksimal 25 MB — coba foto yang lebih kecil."),
)


def _friendly_file_error(message: str) -> str:
    for needle, text in _FILE_ERROR_TEXT:
        if needle in message:
            return text
    return "File itu tidak bisa dipakai. Coba foto atau PDF lain."


@web.post("/cases/{case_id}/intake")
async def intake_submit(case_id: str, request: Request):
    form = await request.form()
    intake = _svc(request)["intake"]
    files = form.getlist("files")
    text = str(form.get("text") or "").strip()
    url = str(form.get("url") or "").strip()
    has_file = any(getattr(upload, "filename", None) for upload in files)
    if not has_file and not text and not url:
        return RedirectResponse(f"/cases/{case_id}/intake?notice=kosong", status_code=303)
    try:
        for upload in files:
            if getattr(upload, "filename", None):
                data = await upload.read()  # type: ignore[union-attr]
                if data:
                    intake.upload_bytes(case_id, _sid(request), upload.filename, data)  # type: ignore[union-attr]
        if text:
            intake.add_text(case_id, _sid(request), text)
        if url:
            intake.add_url(case_id, _sid(request), url)
    except AppError as exc:
        case = _svc(request)["cases"].get_owned(case_id, _sid(request))
        evidence = EvidenceRepository(request.state.db).list_for_case(case_id)
        return TEMPLATES.TemplateResponse(
            "intake.html",
            {
                "request": request,
                "case": case,
                "evidence": [evidence_public(e) for e in evidence],
                "can_delete_evidence": case.state in {State.NEW, State.INGESTING, State.REVIEW_REQUIRED, State.EXTRACTING},
                "summary": case_summary(request.state.db, case),
                "notice": "file-gagal",
                "notice_text": _friendly_file_error(exc.message),
            },
            status_code=exc.http_status,
        )
    # Explicit action trigger (POST): start the pipeline here so that
    # GET /processing stays a pure, refresh-safe state render.
    try:
        from app.api.router import _kick_orchestrator

        _kick_orchestrator(request, case_id)
    except Exception:
        pass
    return RedirectResponse(f"/cases/{case_id}/processing", status_code=303)


@web.post("/cases/{case_id}/evidence/{evidence_id}/delete")
def delete_evidence_web(case_id: str, evidence_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    if case.state not in {State.NEW, State.INGESTING, State.REVIEW_REQUIRED, State.EXTRACTING}:
        return RedirectResponse(f"/cases/{case_id}/intake", status_code=303)
    repo = EvidenceRepository(request.state.db)
    item = repo.get(evidence_id)
    if item.case_id != case_id:
        return RedirectResponse(f"/cases/{case_id}/intake", status_code=303)
    request.app.state.container.storage.delete_key(case_id, item.storage_key)
    repo.delete(evidence_id)
    return RedirectResponse(f"/cases/{case_id}/intake", status_code=303)


@web.post("/cases/{case_id}/baru")
def reset_case(case_id: str, request: Request):
    _svc(request)["orchestrator"].run_tool(
        case_id,
        new_id("run"),
        "purge_case",
        {"confirmation": "PURGE", "user_initiated": True, "session_id": _sid(request)},
    )
    return RedirectResponse("/", status_code=303)


@web.get("/cases/{case_id}/processing")
def processing(case_id: str, request: Request):
    # Read-only render: the pipeline is triggered by POST /intake or
    # POST /api/v1/cases/{id}/runs, so refresh/back never mutates state.
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    has_evidence = bool(EvidenceRepository(request.state.db).list_for_case(case_id))
    return TEMPLATES.TemplateResponse(
        "processing.html",
        {
            "request": request,
            "case": case,
            "has_evidence": has_evidence,
        },
    )


@web.get("/cases/{case_id}/review")
def review(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    facts = FactRepository(request.state.db).list_for_case(case_id)
    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    evidence = EvidenceRepository(request.state.db).list_for_case(case_id)
    facts_pub = [fact_public(f) for f in facts]
    conflicts_pub = [conflict_public(c) for c in conflicts]
    blocking = [c for c in conflicts_pub if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    blocking_ids = {fid for c in blocking for fid in c["fact_ids"]}
    visible = []
    seen_keys: set[tuple[str, str]] = set()
    for f in facts_pub:
        if f["review_status"] == "REJECTED" or (blocking and f["fact_id"] in blocking_ids):
            continue
        key = (str(f["type"]), str(f.get("normalized_value") or f["raw_value"]))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        visible.append(f)
    return TEMPLATES.TemplateResponse(
        "review.html",
        {
            "request": request,
            "case": case,
            "facts": visible,
            "facts_by_id": {f["fact_id"]: f for f in facts_pub},
            "evidence_names": {e.evidence_id: e.original_name_display for e in evidence},
            "conflicts": conflicts_pub,
            "has_blocking": bool(blocking),
            "pairing_units": [] if blocking else _pairing_cards(request.state.db, case_id),
            "summaries": []
            if blocking
            else _tx_summaries(
                request.state.db,
                case_id,
                {e.evidence_id: e.original_name_display for e in evidence},
            ),
            "notice": request.query_params.get("notice", ""),
            "pairing_key": new_id("pair"),
        },
    )


@web.post("/cases/{case_id}/pairing/{unit_id}")
async def submit_pairing(case_id: str, unit_id: str, request: Request):
    from app.domain.errors import StaleCaseVersion

    _svc(request)["cases"].get_owned(case_id, _sid(request))
    form = await request.form()
    evidence_id = str(form.get("evidence_id") or "")
    pairings: list[dict[str, str]] = []
    # One-decision form contract: per destination index, chosen amount + time.
    for index in range(4):
        dest = str(form.get(f"dest_id_{index}") or "")
        if not dest:
            # legacy row contract (selects per row)
            dest = str(form.get(f"destination_fact_id_{index}") or "")
            amount = str(form.get(f"amount_fact_id_{index}") or "")
            when = str(form.get(f"datetime_fact_id_{index}") or "")
        else:
            amount = str(form.get(f"amount_for_{index}") or "")
            when = str(form.get(f"time_for_{index}") or "")
        if dest and amount:
            row = {"destination_fact_id": dest, "amount_fact_id": amount}
            if when:
                row["datetime_fact_id"] = when
            pairings.append(row)
    try:
        from app.api.router import post_unit_mapping

        post_unit_mapping(
            case_id,
            unit_id,
            request,
            {
                "target_evidence_id": evidence_id,
                "pairings": pairings,
                "reason": "tinjauan",
                "expected_version": str(form.get("expected_version") or ""),
                "idempotency_key": str(form.get("idempotency_key") or ""),
            },
        )
    except StaleCaseVersion:
        return RedirectResponse(f"/cases/{case_id}/review?notice=pairing-basi", status_code=303)
    except AppError:
        return RedirectResponse(f"/cases/{case_id}/review?notice=pairing-gagal", status_code=303)
    return RedirectResponse(f"/cases/{case_id}/review?notice=pairing-ok", status_code=303)


@web.get("/cases/{case_id}/readiness")
def readiness_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    from app.infrastructure.repositories import UnitMappingRepository
    from app.services.next_action import next_action_to_dict, recommend_next_action
    from app.services.readiness import assess, assess_units, public_report
    from app.services.reporting_units import compile_reporting_units

    try:
        if case.state == State.REVIEW_REQUIRED:
            _svc(request)["inspect"].validate_case_facts(case_id)
            case = CaseRepository(request.state.db).get(case_id)
        if case.state == State.READY_FOR_ACTION:
            from app.api.router import _kick_orchestrator
            from app.infrastructure.jobs import JobQueue

            # Refresh-safe: never enqueue a duplicate orchestrate job while
            # one is still pending/running for this case.
            jobs = JobQueue(request.state.db).list_for_case(case_id)
            if not any(j.kind == "orchestrate" and j.status in {"pending", "running"} for j in jobs):
                _kick_orchestrator(request, case_id)
            case = CaseRepository(request.state.db).get(case_id)
    except Exception:
        case = CaseRepository(request.state.db).get(case_id)

    facts = FactRepository(request.state.db).list_for_case(case_id)
    evidence = EvidenceRepository(request.state.db).list_for_case(case_id)
    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    transactions = TransactionRepository(request.state.db).list_for_case(case_id)
    report = public_report(
        assess(case_id=case_id, route=case.route, facts=facts, conflicts=conflicts, evidence=evidence, transactions=transactions)
    )
    # try rescue units
    units = []
    units_report = None
    next_action = None
    try:
        mappings = UnitMappingRepository(request.state.db).list_for_case(case_id)
        decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
        units = compile_reporting_units(case_id, facts, evidence, decs if decs else None)
        if units:
            units_report = assess_units(case_id=case_id, units=units, facts=facts, evidence=evidence, conflicts=conflicts, route=case.route)
            next_action = next_action_to_dict(
                recommend_next_action(
                    case_id=case_id,
                    units=units,
                    conflicts=conflicts,
                    readiness_by_unit=units_report.get("readiness_by_unit"),
                    incident_police_ready=(units_report.get("incident_police", {}).get("status") == "READY"),
                )
            )
    except Exception:
        units = []
        units_report = None
        next_action = None
    next_view = None
    if next_action:
        next_view = {
            "label": soften(next_action.get("label")),
            "reason": soften(next_action.get("reason")),
        }
    gaps = _gap_texts(report, units_report)
    evidence_names = {e.evidence_id: e.original_name_display for e in evidence}
    summaries = _tx_summaries(request.state.db, case_id, evidence_names)
    tx_cards = []
    has_blocking = False
    needs_evidence = False
    ready_count = 0
    if units and units_report:
        reps = {r.get("unit_id"): r for r in units_report.get("units") or []}
        by_unit = units_report.get("readiness_by_unit") or {}
        for tx in summaries:
            rep = reps.get(tx["unit_id"], {})
            ch_status = by_unit.get(tx["unit_id"], {})
            missing_review: list[str] = []
            missing_evidence: list[str] = []
            missing_info: list[str] = []
            for ch in rep.get("channels", []) or []:
                for ck in ch.get("checks", []) or []:
                    if ck.get("status") not in {"MISSING", "CONFLICT"}:
                        continue
                    text = soften(ck.get("action") or ck.get("label") or "")
                    if not text:
                        continue
                    check_id = str(ck.get("check_id") or "")
                    is_evidence = "EVIDENCE" in check_id or "COMMUNICATION" in check_id
                    if ck.get("blocking"):
                        if is_evidence:
                            needs_evidence = True
                            if text not in missing_evidence:
                                missing_evidence.append(text)
                        else:
                            has_blocking = True
                            if text not in missing_review:
                                missing_review.append(text)
                    elif text not in missing_info:
                        missing_info.append(text)
            financial = [ch_status.get("BANK_PJP"), ch_status.get("IASC")]
            ready = bool(financial) and all(s == "READY" for s in financial) and not missing_review
            if ready:
                ready_count += 1
            tx_cards.append(
                {
                    **tx,
                    "ready": ready,
                    "channels": [
                        {"label": "Bank", "status": ch_status.get("BANK_PJP", "")},
                        {"label": "IASC", "status": ch_status.get("IASC", "")},
                    ],
                    "missing_review": missing_review,
                    "missing_evidence": missing_evidence,
                    "missing_info": missing_info,
                }
            )
        for ck in (units_report.get("incident_police", {}) or {}).get("checks", []) or []:
            if ck.get("status") in {"MISSING", "CONFLICT"} and ck.get("blocking"):
                check_id = str(ck.get("check_id") or "")
                if "EVIDENCE" in check_id or "COMMUNICATION" in check_id or "PROVENANCE" in check_id:
                    needs_evidence = True
                else:
                    has_blocking = True
    return TEMPLATES.TemplateResponse(
        "readiness.html",
        {
            "request": request,
            "case": case,
            "next_view": next_view,
            "gaps": gaps,
            "tx_cards": tx_cards,
            "has_blocking": has_blocking,
            "needs_evidence": needs_evidence,
            "ready_count": ready_count,
        },
    )


@web.get("/cases/{case_id}/result")
def result(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    try:
        if case.state == State.REVIEW_REQUIRED:
            _svc(request)["inspect"].validate_case_facts(case_id)
            case = CaseRepository(request.state.db).get(case_id)
        if case.state == State.REVIEW_REQUIRED:
            return RedirectResponse(f"/cases/{case_id}/review", status_code=303)
        if case.route.value != "PRE_INCIDENT_CHECK":
            # Post-incident plan lives on the rescue dashboard now.
            return RedirectResponse(f"/cases/{case_id}/readiness", status_code=303)
        if case.state == State.READY_FOR_ACTION:
            from app.api.router import _kick_orchestrator
            from app.infrastructure.jobs import JobQueue

            # Refresh-safe: never enqueue a duplicate orchestrate job while
            # one is still pending/running for this case.
            jobs = JobQueue(request.state.db).list_for_case(case_id)
            if not any(j.kind == "orchestrate" and j.status in {"pending", "running"} for j in jobs):
                _kick_orchestrator(request, case_id)
            case = CaseRepository(request.state.db).get(case_id)
    except Exception:
        case = CaseRepository(request.state.db).get(case_id)
    actions = ActionRepository(request.state.db).list_for_case(case_id)
    digest = ""
    try:
        _, digest = _svc(request)["approval"].current_snapshot(case_id)
    except Exception:
        digest = ""
    return TEMPLATES.TemplateResponse(
        "result.html",
        {"request": request, "case": case, "actions": actions, "snapshot_hash": digest},
    )


@web.get("/cases/{case_id}/approval")
def approval_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    _, digest = _svc(request)["approval"].current_snapshot(case_id)
    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    blocking = [c for c in conflicts if c.severity.value == "BLOCKING" and c.status.value == "OPEN"]
    ambiguous = any(str(getattr(u.mapping_status, "value", u.mapping_status)) == "AMBIGUOUS" for u in _case_units(request.state.db, case_id))
    evidence_names = {e.evidence_id: e.original_name_display for e in EvidenceRepository(request.state.db).list_for_case(case_id)}
    scope_units = _tx_summaries(request.state.db, case_id, evidence_names)
    pending: list[str] = []
    for u in _case_units(request.state.db, case_id):
        if str(getattr(u.mapping_status, "value", u.mapping_status)) == "AMBIGUOUS":
            pending.append(soften(getattr(u, "mapping_reason", "") or "transaksi yang belum terpasang"))
    return TEMPLATES.TemplateResponse(
        "approval.html",
        {
            "request": request,
            "case": case,
            "snapshot_hash": digest,
            "blocking": blocking,
            "ambiguous": ambiguous,
            "scope_units": scope_units,
            "pending": pending,
            "notice": request.query_params.get("notice", ""),
            "idempotency_key": new_id("apprweb"),
        },
    )


@web.post("/cases/{case_id}/approval")
def submit_approval(
    case_id: str,
    request: Request,
    snapshot_hash: str = Form(""),
    accepted_notice: str = Form(""),
    idempotency_key: str = Form(""),
):
    import json

    from app.api.router import _idempotency, _kick_orchestrator, _store_idem
    from app.domain.errors import AppError

    notice = accepted_notice in {"1", "on", "true", "yes"}
    if not idempotency_key:
        return RedirectResponse(f"/cases/{case_id}/approval?notice=coba-lagi", status_code=303)
    idem_payload = json.dumps({"snapshot_hash": snapshot_hash, "accepted_notice": notice}, sort_keys=True)
    try:
        if _idempotency(request, case_id, idempotency_key, idem_payload):
            # Double submit with the same key: approval already recorded.
            return RedirectResponse(f"/cases/{case_id}/artifacts", status_code=303)
        _svc(request)["approval"].approve(case_id, _sid(request), snapshot_hash, notice)
    except ValidationFailed as exc:
        if "pasangan" in str(exc.message):
            return RedirectResponse(f"/cases/{case_id}/review?notice=pairing-gagal", status_code=303)
        return RedirectResponse(f"/cases/{case_id}/approval?notice=coba-lagi", status_code=303)
    except AppError:
        return RedirectResponse(f"/cases/{case_id}/approval?notice=coba-lagi", status_code=303)
    _store_idem(request, idempotency_key, case_id, idem_payload, {"status": "approved"})
    _kick_orchestrator(request, case_id)
    return RedirectResponse(f"/cases/{case_id}/artifacts", status_code=303)


@web.get("/cases/{case_id}/paket.zip")
def download_pack(case_id: str, request: Request):
    from app.api.router import download_all_artifacts

    return download_all_artifacts(case_id, request)


@web.get("/cases/{case_id}/artifacts")
def artifacts_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    items = ArtifactRepository(request.state.db).list_for_case(case_id)
    url = request.app.state.container.settings.official_iasc_url
    pub = [artifact_public(a) for a in items]
    roles = [
        ("bank", "Untuk bank", {"BANK_HANDOFF_PACK", "UNIT_BANK_PACK"}),
        ("iasc", "Untuk IASC", {"IASC_HANDOFF_PACK", "UNIT_IASC_PACK"}),
        ("police", "Ringkasan seluruh kejadian", {"POLICE_HANDOFF_PACK", "ACTION_PLAN"}),
        ("brief", "Ringkasan & cek", {"VERIFICATION_BRIEF", "READINESS_REPORT", "CHECKLIST", "EVIDENCE_PACK"}),
    ]
    groups = []
    used: set[str] = set()
    for key, title, types in roles:
        members = [a for a in pub if a["type"] in types and a["downloadable"]]
        for a in members:
            used.add(a["artifact_id"])
        if members:
            groups.append({"key": key, "title": title, "files": members})
    rest = [a for a in pub if a["artifact_id"] not in used]
    pack = next((a for a in pub if a["type"] == "CASE_ZIP" and a["downloadable"]), None)
    return TEMPLATES.TemplateResponse(
        "artifacts.html",
        {
            "request": request,
            "case": case,
            "artifacts": pub,
            "groups": groups,
            "rest": rest,
            "pack": pack,
            "official_url": url,
            "domain": "iasc.ojk.go.id",
        },
    )


@web.get("/cases/{case_id}/receipt")
def receipt_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    record = ReceiptRepository(request.state.db).get_for_case(case_id)
    return TEMPLATES.TemplateResponse(
        "receipt.html",
        {"request": request, "case": case, "receipt": record},
    )


@web.get("/cases/{case_id}/workspace")
def workspace_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    return TEMPLATES.TemplateResponse(
        "workspace.html",
        {"request": request, "case": case},
    )


@web.get("/demo/dashboard")
def dashboard(request: Request):
    return RedirectResponse("/", status_code=303)
