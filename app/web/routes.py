from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.helpers import artifact_public, case_summary, conflict_public, evidence_public, fact_public
from app.deps import services_from
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
)
from app.services.ids import new_id
from app.web.labels import human, soften

web = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["human"] = human
TEMPLATES.env.filters["soften"] = soften
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
        },
    )


@web.post("/cases/{case_id}/intake")
async def intake_submit(case_id: str, request: Request):
    form = await request.form()
    intake = _svc(request)["intake"]
    files = form.getlist("files")
    for upload in files:
        if getattr(upload, "filename", None):
            data = await upload.read()  # type: ignore[union-attr]
            if data:
                intake.upload_bytes(case_id, _sid(request), upload.filename, data)  # type: ignore[union-attr]
    text = str(form.get("text") or "").strip()
    url = str(form.get("url") or "").strip()
    if text:
        intake.add_text(case_id, _sid(request), text)
    if url:
        intake.add_url(case_id, _sid(request), url)
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
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    if case.state in {State.NEW, State.INGESTING, State.EXTRACTING} and request.app.state.container.settings.sync_jobs:
        from app.services.ids import new_id

        _svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
        case = CaseRepository(request.state.db).get(case_id)
    return TEMPLATES.TemplateResponse(
        "processing.html",
        {
            "request": request,
            "case": case,
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
        },
    )


@web.get("/cases/{case_id}/readiness")
def readiness_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    from app.infrastructure.repositories import UnitMappingRepository
    from app.services.next_action import next_action_to_dict, recommend_next_action
    from app.services.readiness import assess, assess_units, public_report
    from app.services.reporting_units import compile_reporting_units

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
    return TEMPLATES.TemplateResponse(
        "readiness.html",
        {
            "request": request,
            "case": case,
            "next_view": next_view,
            "gaps": gaps,
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
        if case.state == State.READY_FOR_ACTION:
            from app.api.router import _kick_orchestrator

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
    return TEMPLATES.TemplateResponse(
        "approval.html",
        {"request": request, "case": case, "snapshot_hash": digest, "blocking": blocking},
    )


@web.get("/cases/{case_id}/artifacts")
def artifacts_page(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    items = ArtifactRepository(request.state.db).list_for_case(case_id)
    url = request.app.state.container.settings.official_iasc_url
    return TEMPLATES.TemplateResponse(
        "artifacts.html",
        {
            "request": request,
            "case": case,
            "artifacts": [artifact_public(a) for a in items],
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


@web.get("/demo/dashboard")
def dashboard(request: Request):
    return TEMPLATES.TemplateResponse("dashboard.html", {"request": request, "title": "Dashboard VPS"})
