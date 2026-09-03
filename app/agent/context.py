"""Konteks terstruktur Rescue Agent — kecil, tanpa PII mentah.

Jangan lempar seluruh DB ke model: hanya ringkasan yang dibutuhkan untuk
memilih tool dan menyusun panduan. Rekening selalu tersamar.
"""

from __future__ import annotations

from typing import Any

from app.agent.formatting import format_rupiah, mask_account
from app.domain.models import FactType, MappingStatus
from app.domain.states import State
from app.infrastructure.repositories import (
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    EvidenceRepository,
    FactRepository,
    UnitMappingRepository,
)
from app.services.next_action import next_action_to_dict, recommend_next_action
from app.services.readiness import assess_units
from app.services.reporting_units import compile_reporting_units

_CANDIDATE_TYPES = {FactType.AMOUNT, FactType.ACCOUNT, FactType.PJP, FactType.DATETIME}

_QUICK_BY_STATE: dict[str, list[str]] = {
    "NEW": ["Saya harus apa?", "Tunjukkan yang kurang"],
    "INGESTING": ["Saya harus apa?", "Tunjukkan yang kurang"],
    "EXTRACTING": ["Saya harus apa?", "Tunjukkan yang kurang"],
    "REVIEW_REQUIRED": ["Saya harus apa?", "Tunjukkan yang kurang", "Bantu konfirmasi transaksi"],
    "READY_FOR_ACTION": ["Saya harus apa?", "Tunjukkan yang kurang", "Siapkan laporan", "Buka workspace"],
    "WAITING_APPROVAL": ["Apa yang akan dikirim?", "Buka portal resmi"],
    "GENERATING": ["Apa yang akan dikirim?", "Buka portal resmi"],
    "VERIFYING": ["Apa yang akan dikirim?", "Buka portal resmi"],
    "HANDOFF_READY": ["Apa yang akan dikirim?", "Buka portal resmi"],
    "RECEIPT_RECORDED": ["Saya harus apa?"],
    "COMPLETE": ["Saya harus apa?"],
    "FAILED_SAFE": ["Saya harus apa?", "Tunjukkan yang kurang"],
}


def build_agent_context(db: Any, case_id: str) -> dict[str, Any]:
    """Susun konteks JSON-serializable untuk satu kasus."""
    case = CaseRepository(db).get(case_id)
    facts = FactRepository(db).list_for_case(case_id)
    evidence = EvidenceRepository(db).list_for_case(case_id)
    conflicts = ConflictRepository(db).list_for_case(case_id)
    mappings = UnitMappingRepository(db).list_for_case(case_id)
    decs = [
        {"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings}
        for m in mappings
    ]
    units = compile_reporting_units(case_id, facts, evidence, decs if decs else None)
    readiness = assess_units(
        case_id=case_id, units=units, facts=facts, evidence=evidence, conflicts=conflicts, route=case.route
    )
    action = recommend_next_action(
        case_id=case_id,
        units=units,
        conflicts=conflicts,
        readiness_by_unit=readiness.get("readiness_by_unit"),
        incident_police_ready=(readiness.get("incident_police", {}).get("status") == "READY"),
    )
    artifacts = ArtifactRepository(db).list_for_case(case_id)
    facts_by_id = {f.fact_id: f for f in facts}

    unit_views = []
    for index, unit in enumerate(sorted(units, key=lambda u: u.unit_id), start=1):
        view: dict[str, Any] = {
            "index": index,
            "unit_id": unit.unit_id,
            "destination_masked": mask_account(unit.destination_account) if unit.destination_account else None,
            "amount": unit.amount,
            "amount_text": format_rupiah(unit.amount),
            "transferred_at": unit.transferred_at,
            "mapping_status": unit.mapping_status.value,
            "fact_ids": list(unit.fact_ids),
            "evidence_ids": list(unit.evidence_ids),
            "readiness": (readiness.get("readiness_by_unit") or {}).get(unit.unit_id, {}),
        }
        if unit.mapping_status == MappingStatus.AMBIGUOUS:
            candidates = []
            for fid in unit.fact_ids:
                fact = facts_by_id.get(fid)
                if fact is None or fact.type not in _CANDIDATE_TYPES:
                    continue
                raw = fact.normalized_value or fact.raw_value
                if fact.type in {FactType.ACCOUNT, FactType.PJP}:
                    raw = mask_account(raw)
                candidates.append(
                    {
                        "fact_id": fact.fact_id,
                        "type": fact.type.value,
                        "value": str(raw),
                        "review_status": fact.review_status.value,
                    }
                )
            view["candidates"] = candidates
        unit_views.append(view)

    open_conflicts = [
        {
            "conflict_id": c.conflict_id,
            "type": c.type.value,
            "severity": c.severity.value,
            "fact_ids": list(c.fact_ids),
        }
        for c in conflicts
        if c.status.value == "OPEN"
    ]

    return {
        "case": {
            "case_id": case_id,
            "state": case.state.value,
            "route": case.route.value,
            "version": case.version,
        },
        "units": unit_views,
        "unit_ids": [u["unit_id"] for u in unit_views],
        "readiness_overall": readiness.get("overall_status"),
        "conflicts_open": open_conflicts,
        "next_action": next_action_to_dict(action),
        "approval_present": bool(case.approved_snapshot_hash),
        "artifacts": [
            {"artifact_id": a.artifact_id, "type": a.type.value, "verify": a.verify_status.value}
            for a in artifacts
        ],
        "evidence_count": len(evidence),
        "quick_actions": list(_QUICK_BY_STATE.get(case.state.value, ["Saya harus apa?"])),
    }


def state_of(context: dict[str, Any]) -> State:
    return State(context["case"]["state"])
