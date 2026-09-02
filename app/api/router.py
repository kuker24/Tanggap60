from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, Header, Request, UploadFile
from fastapi.responses import Response

from app.api.helpers import (
    artifact_public,
    case_summary,
    conflict_public,
    evidence_public,
    fact_public,
)
from app.config import HANDOFF_ALLOWLIST
from app.deps import services_from
from app.domain.errors import (
    Forbidden,
    IdempotencyConflict,
    InvalidStateTransition,
    ResourceLimit,
    ValidationFailed,
)
from app.domain.models import VerifyStatus
from app.domain.policies import sha256_text
from app.domain.states import DeclaredCondition, Mode, State
from app.hermes.tools.catalog import TOOL_SPECS
from app.infrastructure.jobs import JobQueue
from app.infrastructure.repositories import (
    ActionRepository,
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    EventRepository,
    EvidenceRepository,
    FactRepository,
    IdempotencyRepository,
    ReceiptRepository,
    UnitMappingRepository,
)
from app.infrastructure.resources import guard_resources
from app.services.ids import new_id

api = APIRouter(prefix="/api/v1")


def svc(request: Request) -> dict[str, Any]:
    return services_from(request.state.db, request.app.state.container)


def sid(request: Request) -> str:
    return str(request.state.session_id)


@api.post("/cases", status_code=201)
def create_case(request: Request, payload: dict[str, str]) -> dict[str, Any]:
    mode = Mode(payload.get("mode", "DEMO"))
    condition = DeclaredCondition(payload.get("declared_condition", "UNKNOWN"))
    case, token = svc(request)["cases"].create(mode=mode, condition=condition, session_id=sid(request))
    body = case_summary(request.state.db, case, extra={"case_token": token})
    return body


@api.get("/cases/{case_id}")
def get_case(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    return case_summary(request.state.db, case)


@api.get("/cases/{case_id}/readiness")
def get_readiness(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    from app.infrastructure.repositories import (
        ConflictRepository,
        EvidenceRepository,
        FactRepository,
        TransactionRepository,
    )
    from app.services.readiness import assess, public_report

    report = assess(
        case_id=case_id,
        route=case.route,
        facts=FactRepository(request.state.db).list_for_case(case_id),
        conflicts=ConflictRepository(request.state.db).list_for_case(case_id),
        evidence=EvidenceRepository(request.state.db).list_for_case(case_id),
        transactions=TransactionRepository(request.state.db).list_for_case(case_id),
    )
    return public_report(report)


@api.delete("/cases/{case_id}")
def delete_case(case_id: str, request: Request, payload: dict[str, str] | None = None) -> dict[str, str]:
    confirmation = (payload or {}).get("confirmation", "")
    result = svc(request)["orchestrator"].run_tool(
        case_id,
        new_id("run"),
        "purge_case",
        {"confirmation": confirmation, "user_initiated": True, "session_id": sid(request)},
    )
    return {"status": str(result.get("status") or "PURGED"), "case_id": case_id, "tool_name": "purge_case"}


@api.post("/cases/{case_id}/evidence", status_code=202)
async def upload_evidence(
    case_id: str,
    request: Request,
    files: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    intake = svc(request)["intake"]
    saved = []
    if files:
        for upload in files:
            data = await upload.read()
            record = intake.upload_bytes(case_id, sid(request), upload.filename or "upload.bin", data)
            saved.append(evidence_public(record))
    return {"evidence": saved}


@api.post("/cases/{case_id}/evidence/text", status_code=202)
def upload_text(case_id: str, request: Request, payload: dict[str, str]) -> dict[str, Any]:
    if payload.get("url"):
        record = svc(request)["intake"].add_url(case_id, sid(request), payload["url"])
    else:
        record = svc(request)["intake"].add_text(case_id, sid(request), payload.get("text", ""))
    return evidence_public(record)


@api.get("/cases/{case_id}/evidence")
def list_evidence(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    items = EvidenceRepository(request.state.db).list_for_case(case_id)
    return {"evidence": [evidence_public(i) for i in items]}


@api.delete("/cases/{case_id}/evidence/{evidence_id}")
def delete_evidence(case_id: str, evidence_id: str, request: Request) -> dict[str, str]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    if case.state not in {State.NEW, State.INGESTING, State.REVIEW_REQUIRED, State.EXTRACTING}:
        raise InvalidStateTransition("tidak bisa hapus bukti")
    repo = EvidenceRepository(request.state.db)
    item = repo.get(evidence_id)
    request.app.state.container.storage.delete_key(case_id, item.storage_key)
    repo.delete(evidence_id)
    return {"status": "deleted"}


def _scoped_key(case_id: str, key: str) -> str:
    return f"{case_id}:{key}"


def _idempotency(request: Request, case_id: str, key: str | None, payload: str) -> dict[str, Any] | None:
    if not key:
        raise ValidationFailed("Idempotency-Key wajib")
    repo = IdempotencyRepository(request.state.db)
    digest = sha256_text(payload)
    existing = repo.get(_scoped_key(case_id, key))
    if existing:
        if existing.payload_hash != digest:
            raise IdempotencyConflict("idempotency key dipakai payload lain")
        return json.loads(existing.response_json)
    return None


def _store_idem(request: Request, key: str, case_id: str, payload: str, response: dict[str, Any]) -> None:
    from app.services.cases import now_utc

    IdempotencyRepository(request.state.db).add(
        _scoped_key(case_id, key), case_id, sha256_text(payload), json.dumps(response), now_utc()
    )


@api.post("/cases/{case_id}/runs", status_code=202)
def start_run(
    case_id: str,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    cached = _idempotency(request, case_id, idempotency_key, "run")
    if cached:
        return cached
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    settings = request.app.state.container.settings
    guard_resources(settings, str(request.app.state.container.storage.root))
    run_id = new_id("run")
    queue = JobQueue(request.state.db)
    if queue.depth() > 2:
        raise ResourceLimit("antrean pekerjaan penuh")
    job_id = queue.enqueue(
        case_id=case_id,
        run_id=run_id,
        kind="orchestrate",
        idempotency_key=_scoped_key(case_id, idempotency_key or run_id),
    )
    if settings.sync_jobs:
        result = svc(request)["orchestrator"].run_until_pause(case_id, run_id)
        queue.finish(job_id, result, result.get("status") == "OK")
        body = {"run_id": run_id, "job_id": job_id, **result, "state": CaseRepository(request.state.db).get(case_id).state.value}
    else:
        body = {"run_id": run_id, "job_id": job_id, "status": "queued", "state": case.state.value}
    _store_idem(request, idempotency_key or run_id, case_id, "run", body)
    return body


@api.get("/cases/{case_id}/runs/{run_id}")
def get_run(case_id: str, run_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    events = [e for e in EventRepository(request.state.db).list_for_case(__import__("app.infrastructure.logging", fromlist=["hash_id"]).hash_id(case_id)) if e.run_id == run_id]
    return {
        "run_id": run_id,
        "events": [
            {
                "tool_name": e.tool_name,
                "duration_ms": e.duration_ms,
                "result_code": e.result_code,
                "state_after": e.state_after,
            }
            for e in events
        ],
    }


@api.post("/cases/{case_id}/runs/{run_id}/retry", status_code=202)
def retry_run(case_id: str, run_id: str, request: Request) -> dict[str, Any]:
    return start_run(case_id, request, idempotency_key=f"retry-{run_id}-{uuid4().hex[:8]}")


@api.get("/cases/{case_id}/facts")
def list_facts(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    return {"facts": [fact_public(f) for f in FactRepository(request.state.db).list_for_case(case_id)]}


@api.patch("/cases/{case_id}/facts/{fact_id}")
def patch_fact(case_id: str, fact_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    fact = svc(request)["review"].patch_fact(
        case_id,
        sid(request),
        fact_id,
        str(payload.get("action", "")),
        payload.get("value"),
        int(payload.get("expected_version", 0)),
    )
    return fact_public(fact)


@api.get("/cases/{case_id}/conflicts")
def list_conflicts(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    return {
        "conflicts": [conflict_public(c) for c in ConflictRepository(request.state.db).list_for_case(case_id)]
    }


@api.post("/cases/{case_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(case_id: str, conflict_id: str, request: Request, payload: dict[str, Any]) -> dict[str, str]:
    svc(request)["review"].resolve_conflict(
        case_id,
        sid(request),
        conflict_id,
        str(payload["resolution_fact_id"]),
        int(payload.get("expected_version", 0)),
    )
    return {"status": "resolved"}


def _get_units_and_readiness(case_id: str, db) -> tuple[list, dict[str, Any], dict[str, Any]]:
    from app.infrastructure.repositories import ConflictRepository, EvidenceRepository, FactRepository
    from app.services.readiness import assess_units
    from app.services.reporting_units import compile_reporting_units

    case = CaseRepository(db).get(case_id)
    facts = FactRepository(db).list_for_case(case_id)
    evidence = EvidenceRepository(db).list_for_case(case_id)
    conflicts = ConflictRepository(db).list_for_case(case_id)
    # load mapping decisions
    mappings = UnitMappingRepository(db).list_for_case(case_id)
    # convert mappings to dict for compiler: list of {evidence_id, pairings}
    decs_for_compiler = []
    for m in mappings:
        decs_for_compiler.append(
            {"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings}
        )
    units = compile_reporting_units(case_id, facts, evidence, decs_for_compiler if decs_for_compiler else None)
    readiness = assess_units(
        case_id=case_id, units=units, facts=facts, evidence=evidence, conflicts=conflicts, route=case.route
    )
    # attach readiness per unit
    for unit in units:
        rep = next((r for r in readiness["units"] if r["unit_id"] == unit.unit_id), None)
        if rep:
            unit.readiness = rep
    return units, readiness, {"decisions": mappings}


@api.get("/cases/{case_id}/reporting-units")
def list_reporting_units(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    units, readiness, extra = _get_units_and_readiness(case_id, request.state.db)
    from app.services.reporting_units import unit_to_dict

    return {
        "reporting_units": [unit_to_dict(u) for u in units],
        "readiness": readiness,
        "evidence_semantics": __import__("app.services.reporting_units", fromlist=["classify_evidence"]).classify_evidence(
            __import__("app.infrastructure.repositories", fromlist=["EvidenceRepository"]).EvidenceRepository(request.state.db).list_for_case(case_id),
            __import__("app.infrastructure.repositories", fromlist=["FactRepository"]).FactRepository(request.state.db).list_for_case(case_id),
        ),
    }


@api.post("/cases/{case_id}/reporting-units/{unit_id}/mapping")
def post_unit_mapping(case_id: str, unit_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    # validate target evidence and pairings exist
    evidence_id = str(payload.get("target_evidence_id") or payload.get("evidence_id") or "")
    pairings = payload.get("pairings") or payload.get("chosen_pairings") or []
    if not isinstance(pairings, list) or not pairings:
        raise ValidationFailed("pairings required")
    # verify unit exists (compile current units)
    units, _, _ = _get_units_and_readiness(case_id, request.state.db)
    # unit_id may be ambiguous unit id to be resolved
    # store decision
    from app.domain.models import UnitMappingDecision
    from app.services.cases import now_utc
    from app.services.ids import new_id

    decision = UnitMappingDecision(
        decision_id=new_id("map"),
        case_id=case_id,
        unit_id=unit_id,
        target_evidence_id=evidence_id or None,
        chosen_pairings=[dict(p) for p in pairings],
        actor="USER",
        created_at=now_utc(),
        reason=str(payload.get("reason") or ""),
    )
    UnitMappingRepository(request.state.db).add(decision)
    # audit
    from app.domain.models import AuditEventRecord
    from app.infrastructure.logging import hash_id

    EventRepository(request.state.db).add(
        AuditEventRecord(
            event_id=new_id("evt"),
            case_id=hash_id(case_id),
            run_id=None,
            event_type="UNIT_MAPPING_DECIDED",
            state_before=case.state.value,
            state_after=case.state.value,
            tool_name="resolve_unit_mapping",
            tool_version=__import__("app.config", fromlist=["TOOL_VERSION"]).TOOL_VERSION,
            duration_ms=0,
            result_code="USER",
            error_code=None,
            payload_hash=__import__("app.domain.policies", fromlist=["sha256_text"]).sha256_text(unit_id),
            created_at=now_utc(),
            planner="USER",
            execution="LOCAL_TOOL",
        )
    )
    # return updated units
    units2, readiness2, _ = _get_units_and_readiness(case_id, request.state.db)
    from app.services.reporting_units import unit_to_dict

    return {"decision_id": decision.decision_id, "reporting_units": [unit_to_dict(u) for u in units2], "readiness": readiness2}


@api.get("/cases/{case_id}/next-action")
def get_next_action(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    units, readiness, _ = _get_units_and_readiness(case_id, request.state.db)
    from app.infrastructure.repositories import ConflictRepository
    from app.services.next_action import next_action_to_dict, recommend_next_action

    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    action = recommend_next_action(
        case_id=case_id,
        units=units,
        conflicts=conflicts,
        readiness_by_unit=readiness.get("readiness_by_unit"),
        incident_police_ready=(readiness.get("incident_police", {}).get("status") == "READY"),
    )
    return next_action_to_dict(action)


@api.get("/cases/{case_id}/reporting-units/{unit_id}/draft")
def get_unit_draft(case_id: str, unit_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    payload, digest = svc(request)["approval"].unit_snapshot(case_id, unit_id)
    return {"unit_id": unit_id, "snapshot_hash": digest, "draft": payload, "notice_version": __import__("app.config", fromlist=["NOTICE_VERSION"]).NOTICE_VERSION}


@api.post("/cases/{case_id}/reporting-units/{unit_id}/approval")
def post_unit_approval(
    case_id: str,
    unit_id: str,
    request: Request,
    payload: dict[str, Any],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    cached = _idempotency(request, case_id, idempotency_key, json.dumps(payload, sort_keys=True) + unit_id)
    if cached:
        return cached
    record = svc(request)["approval"].approve(case_id, sid(request), str(payload.get("snapshot_hash", "")), bool(payload.get("accepted_notice", False)), target_id=unit_id)
    if request.app.state.container.settings.sync_jobs:
        svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
    body = {"approval_id": record.approval_id, "snapshot_hash": record.snapshot_hash, "unit_id": unit_id, "state": CaseRepository(request.state.db).get(case_id).state.value}
    _store_idem(request, idempotency_key or record.approval_id, case_id, json.dumps(payload, sort_keys=True) + unit_id, body)
    return body


@api.post("/cases/{case_id}/draft")
def build_draft(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    if case.state == State.REVIEW_REQUIRED:
        svc(request)["inspect"].validate_case_facts(case_id)
        case = CaseRepository(request.state.db).get(case_id)
    if case.state == State.READY_FOR_ACTION:
        result = svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
        case = CaseRepository(request.state.db).get(case_id)
        payload, digest = svc(request)["approval"].current_snapshot(case_id)
        return {"state": case.state.value, "snapshot_hash": digest, "draft": payload, **result}
    payload, digest = svc(request)["approval"].current_snapshot(case_id)
    return {"state": case.state.value, "snapshot_hash": digest, "draft": payload}


@api.get("/cases/{case_id}/draft")
def get_draft(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    payload, digest = svc(request)["approval"].current_snapshot(case_id)
    conflicts = ConflictRepository(request.state.db).list_for_case(case_id)
    blocking = [c.conflict_id for c in conflicts if c.severity.value == "BLOCKING" and c.status.value == "OPEN"]
    return {
        "state": case.state.value,
        "snapshot_hash": digest,
        "draft": payload,
        "blocking_conflicts": blocking,
        "notice_version": __import__("app.config", fromlist=["NOTICE_VERSION"]).NOTICE_VERSION,
    }


@api.post("/cases/{case_id}/approval")
def post_approval(
    case_id: str,
    request: Request,
    payload: dict[str, Any],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    cached = _idempotency(request, case_id, idempotency_key, json.dumps(payload, sort_keys=True))
    if cached:
        return cached
    record = svc(request)["approval"].approve(
        case_id,
        sid(request),
        str(payload.get("snapshot_hash", "")),
        bool(payload.get("accepted_notice", False)),
    )
    if request.app.state.container.settings.sync_jobs:
        svc(request)["orchestrator"].run_until_pause(case_id, new_id("run"))
    body = {
        "approval_id": record.approval_id,
        "snapshot_hash": record.snapshot_hash,
        "state": CaseRepository(request.state.db).get(case_id).state.value,
    }
    _store_idem(request, idempotency_key or record.approval_id, case_id, json.dumps(payload, sort_keys=True), body)
    return body


@api.delete("/cases/{case_id}/approval")
def revoke_approval(case_id: str, request: Request) -> dict[str, str]:
    svc(request)["approval"].revoke(case_id, sid(request), "user revoke")
    return {"status": "revoked"}


@api.post("/cases/{case_id}/artifacts")
def compile_artifacts(
    case_id: str,
    request: Request,
    payload: dict[str, Any] | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    body_raw = json.dumps(payload or {}, sort_keys=True)
    cached = _idempotency(request, case_id, idempotency_key, body_raw)
    if cached:
        return cached
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    if not case.approved_snapshot_hash:
        from app.domain.errors import ApprovalRequired

        raise ApprovalRequired("butuh persetujuan")
    items = svc(request)["artifacts"].compile(case_id, case.approved_snapshot_hash)
    result = {"artifacts": [artifact_public(a) for a in items]}
    _store_idem(request, idempotency_key or "art", case_id, body_raw, result)
    return result


@api.get("/cases/{case_id}/artifacts")
def list_artifacts(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    items = ArtifactRepository(request.state.db).list_for_case(case_id)
    return {"artifacts": [artifact_public(a) for a in items]}


@api.get("/cases/{case_id}/artifacts/{artifact_id}/download")
def download_artifact(case_id: str, artifact_id: str, request: Request) -> Response:
    svc(request)["cases"].get_owned(case_id, sid(request))
    item = ArtifactRepository(request.state.db).get(artifact_id)
    if item.case_id != case_id or item.verify_status != VerifyStatus.PASS:
        from app.domain.errors import ArtifactVerifyFailed

        raise ArtifactVerifyFailed("artefak belum lulus verifikasi")
    data = request.app.state.container.storage.read_bytes(case_id, item.storage_key)
    from app.domain.errors import ArtifactVerifyFailed
    from app.domain.policies import sha256_bytes

    if sha256_bytes(data) != item.sha256:
        raise ArtifactVerifyFailed("hash artefak tidak cocok")
    filename = str(item.verify_details.get("filename", artifact_id))
    return Response(
        content=data,
        media_type=item.mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.post("/cases/{case_id}/artifacts/verify")
def verify_artifacts(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    if not case.approved_snapshot_hash:
        from app.domain.errors import ApprovalRequired

        raise ApprovalRequired("butuh persetujuan")
    results = svc(request)["verifier"].verify_case(case_id, case.approved_snapshot_hash)
    return {"results": results}


@api.get("/cases/{case_id}/handoff")
def get_handoff(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    url = request.app.state.container.settings.official_iasc_url
    if url.rstrip("/") not in {u.rstrip("/") for u in HANDOFF_ALLOWLIST}:
        raise Forbidden("URL tidak di allowlist")
    return {
        "official_url": url,
        "domain": "iasc.ojk.go.id",
        "state": case.state.value,
        "server_submission": False,
        "copy": "Isi identitas dan KTP langsung di portal resmi.",
    }


@api.post("/cases/{case_id}/handoff/opened")
def handoff_opened(case_id: str, request: Request) -> dict[str, str]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    from app.domain.models import AuditEventRecord
    from app.infrastructure.logging import hash_id
    from app.services.cases import now_utc

    EventRepository(request.state.db).add(
        AuditEventRecord(
            event_id=new_id("evt"),
            case_id=hash_id(case_id),
            run_id=None,
            event_type="HANDOFF_OPENED_BY_USER",
            state_before=None,
            state_after=None,
            tool_name=None,
            tool_version=None,
            duration_ms=None,
            result_code="USER",
            error_code=None,
            payload_hash=None,
            created_at=now_utc(),
        )
    )
    return {"status": "logged"}


@api.post("/cases/{case_id}/receipt")
def post_receipt(
    case_id: str,
    request: Request,
    payload: dict[str, Any],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    raw = json.dumps(payload, sort_keys=True)
    cached = _idempotency(request, case_id, idempotency_key, raw)
    if cached:
        return cached
    svc(request)["orchestrator"].run_tool(
        case_id,
        new_id("run"),
        "record_handoff_receipt",
        {
            "ticket_text": payload.get("ticket_text"),
            "ocr_text": payload.get("ocr_text"),
            "evidence_id": payload.get("evidence_id"),
            "user_confirms_unreadable": bool(payload.get("user_confirms_unreadable", False)),
        },
    )
    record = ReceiptRepository(request.state.db).get_for_case(case_id)
    if record is None:
        raise ValidationFailed("receipt gagal dicatat")
    if record.official_status != "NOT_VERIFIED":
        record.official_status = "NOT_VERIFIED"
    body = {
        "receipt_id": record.receipt_id,
        "ticket_value_masked": record.ticket_value_masked,
        "format_status": record.format_status.value,
        "local_match_status": record.local_match_status.value,
        "official_status": "NOT_VERIFIED",
        "source": record.source.value,
    }
    _store_idem(request, idempotency_key or record.receipt_id, case_id, raw, body)
    return body


@api.get("/cases/{case_id}/receipt")
def get_receipt(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    record = ReceiptRepository(request.state.db).get_for_case(case_id)
    if record is None:
        return {"receipt": None, "official_status": "NOT_VERIFIED"}
    return {
        "ticket_value_masked": record.ticket_value_masked,
        "format_status": record.format_status.value,
        "local_match_status": record.local_match_status.value,
        "official_status": "NOT_VERIFIED",
        "source": record.source.value,
    }


@api.get("/cases/{case_id}/events")
def list_events(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    from app.infrastructure.logging import hash_id

    events = EventRepository(request.state.db).list_for_case(hash_id(case_id))
    return {
        "events": [
            {
                "event_type": e.event_type,
                "tool_name": e.tool_name,
                "duration_ms": e.duration_ms,
                "result_code": e.result_code,
                "state_after": e.state_after,
                "planner": e.planner,
                "execution": e.execution,
                # separated latency (no breaking, additive)
                "planner_ms": getattr(e, "planner_ms", None),
                "handler_ms": getattr(e, "handler_ms", None),
                "hermes_attempt_1_ms": getattr(e, "hermes_attempt_1_ms", None),
                "hermes_attempt_2_ms": getattr(e, "hermes_attempt_2_ms", None),
                "hermes_sequence_ms": getattr(e, "hermes_sequence_ms", None),
                "ocr_total_ms": getattr(e, "ocr_total_ms", None),
            }
            for e in events
        ]
    }


@api.get("/cases/{case_id}/trace")
def agent_trace(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    from app.hermes.telemetry import REASONING_TOOLS
    from app.infrastructure.logging import hash_id

    events = EventRepository(request.state.db).list_for_case(hash_id(case_id))
    steps = [
        {
            "tool_name": e.tool_name,
            "planner": e.planner or "DETERMINISTIC_SAFE",
            "execution": e.execution or "LOCAL_TOOL",
            "duration_ms": e.duration_ms or 0,
            # separated breakdown (additive, no breaking)
            "planner_ms": getattr(e, "planner_ms", None) or 0,
            "handler_ms": getattr(e, "handler_ms", None) or (e.duration_ms or 0),
            "hermes_attempt_1_ms": getattr(e, "hermes_attempt_1_ms", None) or 0,
            "hermes_attempt_2_ms": getattr(e, "hermes_attempt_2_ms", None) or 0,
            "hermes_sequence_ms": getattr(e, "hermes_sequence_ms", None) or 0,
            "ocr_total_ms": getattr(e, "ocr_total_ms", None) or 0,
            "local_processing_ms": getattr(e, "handler_ms", None) or (e.duration_ms or 0),
        }
        for e in events
        if e.event_type == "TOOL_CALLED" and e.tool_name
    ]
    # aggregates for investigation
    ocr_total_ms = sum(int(s.get("ocr_total_ms") or 0) for s in steps)  # type: ignore
    hermes_sequence_ms = sum(int(s.get("hermes_sequence_ms") or 0) for s in steps)  # type: ignore
    hermes_picker_ms = sum(int(s.get("planner_ms") or 0) for s in steps) - hermes_sequence_ms  # type: ignore
    local_processing_ms = sum(int(s.get("handler_ms") or 0) for s in steps)  # type: ignore
    hermes_attempt_1_ms = sum(int(s.get("hermes_attempt_1_ms") or 0) for s in steps)  # type: ignore
    hermes_attempt_2_ms = sum(int(s.get("hermes_attempt_2_ms") or 0) for s in steps)  # type: ignore
    per_tool_ms = {s["tool_name"]: {"handler_ms": s["handler_ms"], "planner_ms": s["planner_ms"], "total_ms": s["duration_ms"]} for s in steps}
    reasoning_steps = [s for s in steps if s["tool_name"] in REASONING_TOOLS]
    reasoning_fallback = [s for s in reasoning_steps if s["planner"] != "HERMES_CLI"]
    hermes_cli_used = any(step["planner"] == "HERMES_CLI" for step in steps)
    # detailed hermes telemetry per case via hermes instance last state (best-effort)
    hermes = request.app.state.container.hermes
    return {
        "steps": steps,
        "hermes_cli_used": hermes_cli_used,
        "hermes_cli_configured": bool(getattr(hermes, "hermes_cli_configured", False) or request.app.state.container.settings.hermes_bin),
        "hermes_cli_attempted": bool(getattr(hermes, "hermes_cli_attempted", False)),
        "hermes_cli_succeeded": bool(getattr(hermes, "hermes_cli_succeeded", False)) or hermes_cli_used,
        "hermes_fallback_used": len(reasoning_fallback) > 0 or bool(getattr(hermes, "hermes_fallback_used", False)),
        "hermes_failure_reason": getattr(hermes, "hermes_failure_reason", None),
        "hermes_reasoning_success": len(reasoning_fallback) == 0 and hermes_cli_used,
        "reasoning_fallback_count": len(reasoning_fallback),
        "official_status": "NOT_VERIFIED",
        # separated aggregates
        "ocr_total_ms": ocr_total_ms,
        "hermes_sequence_ms": hermes_sequence_ms,
        "hermes_picker_ms": hermes_picker_ms,
        "hermes_attempt_1_ms": hermes_attempt_1_ms,
        "hermes_attempt_2_ms": hermes_attempt_2_ms,
        "local_processing_ms": local_processing_ms,
        "per_tool_ms": per_tool_ms,
    }


@api.get("/agent/tools")
def agent_tools(request: Request) -> dict[str, Any]:
    hermes = request.app.state.container.hermes
    settings = request.app.state.container.settings
    return {
        "tools": list(TOOL_SPECS),
        "hermes_mode": getattr(hermes, "last_mode", "deterministic"),
        "hermes_cli_used": bool(getattr(hermes, "cli_used", False)),
        "hermes_cli_configured": bool(getattr(hermes, "hermes_cli_configured", False) or bool(settings.hermes_bin) or bool(settings.hermes_endpoint)),
        "hermes_bin_configured": bool(settings.hermes_bin),
        "hermes_cli_attempted": bool(getattr(hermes, "hermes_cli_attempted", False)),
        "hermes_cli_succeeded": bool(getattr(hermes, "hermes_cli_succeeded", False)),
        "hermes_fallback_used": bool(getattr(hermes, "hermes_fallback_used", False)),
        "hermes_failure_reason": getattr(hermes, "hermes_failure_reason", None),
    }


@api.get("/cases/{case_id}/actions")
def list_actions(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    actions = ActionRepository(request.state.db).list_for_case(case_id)
    return {
        "actions": [
            {
                "action_id": a.action_id,
                "priority": a.priority.value,
                "channel": a.channel.value,
                "instruction": a.instruction,
                "status": a.status.value,
            }
            for a in actions
        ]
    }


@api.post("/cases/{case_id}/decision")
def user_decision(case_id: str, request: Request, payload: dict[str, str]) -> dict[str, str]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    decision = payload.get("decision", "")
    if decision not in {"CANCELLED_ACTION", "VERIFY_VIA_OFFICIAL_CHANNEL", "PROCEED_BY_USER"}:
        from app.domain.errors import ValidationFailed

        raise ValidationFailed("keputusan tidak dikenal")
    case.user_decision = decision
    if case.state in {State.HANDOFF_READY, State.RECEIPT_RECORDED, State.WAITING_APPROVAL}:
        if case.state != State.COMPLETE:
            try:
                svc(request)["cases"].set_state(case, State.COMPLETE, event_type="DECISION_RECORDED")
            except Exception:
                CaseRepository(request.state.db).save(case)
    else:
        CaseRepository(request.state.db).save(case)
    return {"decision": decision, "actor": "USER"}
