from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import ArtifactRecord, CaseRecord, ConflictRecord, EvidenceRecord, FactRecord
from app.infrastructure.repositories import (
    ConflictRepository,
    EvidenceRepository,
    FactRepository,
)


def case_summary(session: Session, case: CaseRecord, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    facts = FactRepository(session).list_for_case(case.case_id)
    conflicts = ConflictRepository(session).list_for_case(case.case_id)
    evidence = EvidenceRepository(session).list_for_case(case.case_id)
    payload: dict[str, Any] = {
        "case_id": case.case_id,
        "mode": case.mode.value,
        "route": case.route.value,
        "state": case.state.value,
        "version": case.version,
        "route_reason": case.route_reason,
        "ask_loss_question": case.ask_loss_question,
        "approved_snapshot_hash": case.approved_snapshot_hash,
        "evidence_count": len(evidence),
        "fact_count": len(facts),
        "open_conflicts": sum(1 for c in conflicts if c.status.value == "OPEN"),
        "official_status": "NOT_VERIFIED",
    }
    if extra:
        payload.update(extra)
    return payload


def fact_public(fact: FactRecord) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "type": fact.type.value,
        "raw_value": fact.raw_value,
        "normalized_value": fact.normalized_value,
        "criticality": fact.criticality.value,
        "confidence": fact.confidence,
        "review_status": fact.review_status.value,
        "source_evidence_id": fact.source_evidence_id,
        "source_page": fact.source_page,
        "source_locator": fact.source_bbox,
    }


def conflict_public(conflict: ConflictRecord) -> dict[str, Any]:
    return {
        "conflict_id": conflict.conflict_id,
        "type": conflict.type.value,
        "fact_ids": conflict.fact_ids,
        "severity": conflict.severity.value,
        "status": conflict.status.value,
        "resolution_fact_id": conflict.resolution_fact_id,
    }


def evidence_public(item: EvidenceRecord) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "kind": item.kind.value,
        "original_name_display": item.original_name_display,
        "mime": item.mime,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "page_count": item.page_count,
        "status": item.status.value,
        "warning": item.warning,
    }


def artifact_public(item: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": item.artifact_id,
        "type": item.type.value,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "verify_status": item.verify_status.value,
        "source_snapshot_hash": item.source_snapshot_hash,
        "downloadable": item.verify_status.value == "PASS",
    }
