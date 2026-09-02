from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.helpers import artifact_public, case_summary, conflict_public, evidence_public, fact_public
from app.deps import services_from
from app.domain.states import DeclaredCondition, Mode, State
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import (
    ActionRepository,
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    EventRepository,
    EvidenceRepository,
    FactRepository,
    ReceiptRepository,
)
from app.infrastructure.resources import available_ram_mb, process_rss_mb

web = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _sid(request: Request) -> str:
    return str(request.state.session_id)


def _svc(request: Request) -> dict:
    return services_from(request.state.db, request.app.state.container)


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


@web.get("/cases/{case_id}/processing")
def processing(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    if case.state in {State.NEW, State.INGESTING, State.EXTRACTING} and request.app.state.container.settings.sync_jobs:
        from app.services.ids import new_id

        _svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
        case = CaseRepository(request.state.db).get(case_id)
    events = EventRepository(request.state.db).list_for_case(hash_id(case_id))
    return TEMPLATES.TemplateResponse(
        "processing.html",
        {
            "request": request,
            "case": case,
            "events": events,
            "rss": process_rss_mb(),
            "ram": available_ram_mb(),
        },
    )


@web.get("/cases/{case_id}/review")
def review(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    facts = FactRepository(request.state.db).list_for_case(case_id)
    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    evidence = EvidenceRepository(request.state.db).list_for_case(case_id)
    facts_pub = [fact_public(f) for f in facts]
    return TEMPLATES.TemplateResponse(
        "review.html",
        {
            "request": request,
            "case": case,
            "facts": facts_pub,
            "facts_by_id": {f["fact_id"]: f for f in facts_pub},
            "evidence_names": {e.evidence_id: e.original_name_display for e in evidence},
            "conflicts": [conflict_public(c) for c in conflicts],
        },
    )


@web.get("/cases/{case_id}/result")
def result(case_id: str, request: Request):
    case = _svc(request)["cases"].get_owned(case_id, _sid(request))
    if case.state == State.REVIEW_REQUIRED:
        _svc(request)["inspect"].validate_case_facts(case_id)
        case = CaseRepository(request.state.db).get(case_id)
    if case.state == State.READY_FOR_ACTION:
        from app.services.ids import new_id

        _svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
        case = CaseRepository(request.state.db).get(case_id)
    actions = ActionRepository(request.state.db).list_for_case(case_id)
    _, digest = _svc(request)["approval"].current_snapshot(case_id)
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
