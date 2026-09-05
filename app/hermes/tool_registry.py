from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.config import HANDOFF_ALLOWLIST, TOOL_VERSION
from app.domain.errors import InvalidStateTransition, ValidationFailed
from app.domain.states import TOOLS_BY_STATE, State
from app.infrastructure.repositories import (
    ActionRepository,
    CaseRepository,
    EvidenceRepository,
    FactRepository,
)
from app.services.approval import ApprovalService
from app.services.artifacts import ArtifactService
from app.services.inspect import InspectService
from app.services.plan import actions_for_route
from app.services.receipt import ReceiptService
from app.services.urlcheck import analyze_url, reputation_unavailable


class ToolContext:
    def __init__(
        self,
        inspect: InspectService,
        artifacts: ArtifactService,
        approval: ApprovalService,
        receipt: ReceiptService,
        settings_iasc: str,
    ) -> None:
        self.inspect = inspect
        self.artifacts = artifacts
        self.approval = approval
        self.receipt = receipt
        self.iasc = settings_iasc


def execute_tool(name: str, state: State, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    user_initiated = bool(args.get("user_initiated"))
    if name == "purge_case":
        if not user_initiated:
            raise InvalidStateTransition("purge hanya atas permintaan pengguna")
    else:
        allowed = TOOLS_BY_STATE.get(state, ())
        if name not in allowed and not (state == State.REVIEW_REQUIRED and name == "validate_case_facts"):
            raise InvalidStateTransition(f"tool {name} tidak diizinkan pada {state}")
    start = time.perf_counter()
    handler = HANDLERS[name]
    result = handler(args, ctx)
    duration = int((time.perf_counter() - start) * 1000)
    result["tool_name"] = name
    result["tool_version"] = TOOL_VERSION
    result["duration_ms"] = duration
    return result


def _inspect(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.inspect.inspect_evidence(str(args["case_id"]))


def _extract(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.inspect.extract_candidate_facts(str(args["case_id"]))


def _validate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.inspect.validate_case_facts(str(args["case_id"]))


def _pre_brief(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    case_id = str(args["case_id"])
    facts = FactRepository(ctx.inspect.session).list_for_case(case_id)
    evidence = EvidenceRepository(ctx.inspect.session).list_for_case(case_id)
    indicators: list[dict[str, str]] = []
    fetched = False
    for item in evidence:
        if item.kind.value == "URL" or item.mime == "text/uri-list":
            raw = ctx.inspect.storage.read_bytes(case_id, item.storage_key).decode("utf-8")
            found, did_fetch = analyze_url(raw)
            fetched = fetched or did_fetch
            indicators.extend(
                {"name": i.name, "finding": i.finding, "source": i.source, "checked_at": i.checked_at}
                for i in found
            )
    indicators.append(
        {
            "name": reputation_unavailable().name,
            "finding": reputation_unavailable().finding,
            "source": reputation_unavailable().source,
            "checked_at": reputation_unavailable().checked_at,
        }
    )
    case = CaseRepository(ctx.inspect.session).get(case_id)
    ActionRepository(ctx.inspect.session).replace_for_case(
        case_id, actions_for_route(case_id, case.route, facts)
    )
    if fetched:
        raise ValidationFailed("server tidak boleh fetch URL")
    return {
        "indicators": indicators,
        "limitations": "Tidak menjamin aman dan tidak menetapkan penipuan.",
        "facts_used": len(facts),
    }


def _assess(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.infrastructure.repositories import ConflictRepository, TransactionRepository
    from app.services.readiness import assess, public_report

    case_id = str(args["case_id"])
    case = CaseRepository(ctx.inspect.session).get(case_id)
    report = assess(
        case_id=case_id,
        route=case.route,
        facts=FactRepository(ctx.inspect.session).list_for_case(case_id),
        conflicts=ConflictRepository(ctx.inspect.session).list_for_case(case_id),
        evidence=EvidenceRepository(ctx.inspect.session).list_for_case(case_id),
        transactions=TransactionRepository(ctx.inspect.session).list_for_case(case_id),
    )
    public = public_report(report)
    return {
        "overall_status": public["overall_status"],
        "profile_version": public["profile_version"],
        "official_status": "NOT_VERIFIED",
        "channels": [
            {
                "channel": ch["channel"],
                "status": ch["status"],
                "checks_met": ch["checks_met"],
                "checks_total": ch["checks_total"],
            }
            for ch in public["channels"]
        ],
    }


def _compile_units(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.infrastructure.repositories import (
        EvidenceRepository,
        FactRepository,
        UnitMappingRepository,
    )
    from app.services.reporting_units import compile_reporting_units, unit_to_dict

    case_id = str(args["case_id"])
    facts = FactRepository(ctx.inspect.session).list_for_case(case_id)
    evidence = EvidenceRepository(ctx.inspect.session).list_for_case(case_id)
    mappings = UnitMappingRepository(ctx.inspect.session).list_for_case(case_id)
    decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
    units = compile_reporting_units(case_id, facts, evidence, decs if decs else None)
    return {"reporting_units": [unit_to_dict(u) for u in units], "count": len(units)}


def _recommend(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.infrastructure.repositories import (
        ConflictRepository,
        EvidenceRepository,
        FactRepository,
        UnitMappingRepository,
    )
    from app.services.next_action import next_action_to_dict, recommend_next_action
    from app.services.readiness import assess_units
    from app.services.reporting_units import compile_reporting_units

    case_id = str(args["case_id"])
    case = CaseRepository(ctx.inspect.session).get(case_id)
    facts = FactRepository(ctx.inspect.session).list_for_case(case_id)
    evidence = EvidenceRepository(ctx.inspect.session).list_for_case(case_id)
    conflicts = ConflictRepository(ctx.inspect.session).list_for_case(case_id)
    mappings = UnitMappingRepository(ctx.inspect.session).list_for_case(case_id)
    decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
    units = compile_reporting_units(case_id, facts, evidence, decs if decs else None)
    readiness = assess_units(case_id=case_id, units=units, facts=facts, evidence=evidence, conflicts=conflicts, route=case.route)
    action = recommend_next_action(
        case_id=case_id,
        units=units,
        conflicts=conflicts,
        readiness_by_unit=readiness.get("readiness_by_unit"),
        incident_police_ready=(readiness.get("incident_police", {}).get("status") == "READY"),
    )
    return next_action_to_dict(action)


def _post_plan(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    case_id = str(args["case_id"])
    case = CaseRepository(ctx.inspect.session).get(case_id)
    facts = FactRepository(ctx.inspect.session).list_for_case(case_id)
    ActionRepository(ctx.inspect.session).replace_for_case(
        case_id, actions_for_route(case_id, case.route, facts)
    )
    return {"actions": len(ActionRepository(ctx.inspect.session).list_for_case(case_id))}


def _compile(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    case_id = str(args["case_id"])
    snapshot = str(args["approved_snapshot_hash"])
    artifacts = ctx.artifacts.compile(case_id, snapshot)
    return {"artifacts": [a.artifact_id for a in artifacts]}


def _verify(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.verifier import VerifierService

    case_id = str(args["case_id"])
    case = CaseRepository(ctx.inspect.session).get(case_id)
    if not case.approved_snapshot_hash:
        raise ValidationFailed("tidak ada snapshot")
    verifier = VerifierService(ctx.inspect.session, ctx.inspect.storage)
    results = verifier.verify_case(case_id, case.approved_snapshot_hash)
    return {"results": results}


def _handoff(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    url = ctx.iasc
    if url.rstrip("/") not in {u.rstrip("/") for u in HANDOFF_ALLOWLIST}:
        raise ValidationFailed("URL handoff tidak di allowlist")
    return {
        "official_url": url,
        "domain": "iasc.ojk.go.id",
        "submission": False,
        "copy": "Buka sendiri. Tanggap60 tidak mengirim laporan.",
    }


def _receipt(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    record = ctx.receipt.record(
        str(args["case_id"]),
        str(args["session_id"]),
        args.get("ticket_text"),
        args.get("ocr_text"),
        args.get("evidence_id"),
        bool(args.get("user_confirms_unreadable", False)),
        replace=bool(args.get("replace", False)),
    )
    return {
        "format_status": record.format_status.value,
        "local_match_status": record.local_match_status.value,
        "official_status": "NOT_VERIFIED",
    }


def _purge(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.services.purge import PurgeService

    service = PurgeService(ctx.inspect.session, ctx.inspect.cases, ctx.inspect.storage, ctx.inspect.ocr)
    return service.purge(str(args["case_id"]), str(args["session_id"]), str(args.get("confirmation", "")))


HANDLERS: dict[str, Callable[[dict[str, Any], ToolContext], dict[str, Any]]] = {
    "inspect_evidence": _inspect,
    "extract_candidate_facts": _extract,
    "validate_case_facts": _validate,
    "build_preincident_brief": _pre_brief,
    "build_postincident_plan": _post_plan,
    "compile_reporting_units": _compile_units,
    "assess_handoff_readiness": _assess,
    "recommend_next_action": _recommend,
    "compile_artifacts": _compile,
    "verify_artifacts": _verify,
    "prepare_official_handoff": _handoff,
    "record_handoff_receipt": _receipt,
    "purge_case": _purge,
}
