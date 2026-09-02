from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import NOTICE_VERSION, TEMPLATE_VERSION
from app.domain.errors import ApprovalHashMismatch, ApprovalRequired, ValidationFailed
from app.domain.models import ApprovalRecord, ApprovalScope, FactRecord, ReviewStatus
from app.domain.policies import (
    assert_no_blocking_conflicts,
    canonical_json,
    sha256_text,
    snapshot_payload,
)
from app.domain.states import Route, State
from app.infrastructure.repositories import (
    ActionRepository,
    ApprovalRepository,
    CaseRepository,
    ConflictRepository,
    FactRepository,
)
from app.services.cases import CaseService, now_utc
from app.services.ids import new_id


def fact_dict(fact: FactRecord) -> dict[str, object]:
    return {
        "fact_id": fact.fact_id,
        "type": fact.type.value,
        "raw_value": fact.raw_value,
        "normalized_value": fact.normalized_value,
        "review_status": fact.review_status.value,
        "criticality": fact.criticality.value,
        "source_evidence_id": fact.source_evidence_id,
        "source_excerpt_hash": fact.source_excerpt_hash,
    }


class ApprovalService:
    def __init__(self, session: Session, cases: CaseService) -> None:
        self.session = session
        self.cases = cases
        self.case_repo = CaseRepository(session)
        self.facts = FactRepository(session)
        self.conflicts = ConflictRepository(session)
        self.actions = ActionRepository(session)
        self.approvals = ApprovalRepository(session)

    def current_snapshot(self, case_id: str) -> tuple[dict[str, object], str]:
        case = self.case_repo.get(case_id)
        facts = [
            fact_dict(f)
            for f in self.facts.list_for_case(case_id)
            if f.review_status != ReviewStatus.CANDIDATE
        ]
        conflicts = [
            {
                "conflict_id": c.conflict_id,
                "type": c.type.value,
                "fact_ids": c.fact_ids,
                "severity": c.severity.value,
                "status": c.status.value,
                "resolution_fact_id": c.resolution_fact_id,
            }
            for c in self.conflicts.list_for_case(case_id)
        ]
        actions = [
            {
                "action_id": a.action_id,
                "priority": a.priority.value,
                "channel": a.channel.value,
                "instruction": a.instruction,
                "status": a.status.value,
            }
            for a in self.actions.list_for_case(case_id)
        ]
        payload = snapshot_payload(
            facts=facts,
            conflicts=conflicts,
            route=case.route.value,
            actions=actions,
            notice_version=NOTICE_VERSION,
            template_version=TEMPLATE_VERSION,
        )
        digest = sha256_text(canonical_json(payload))
        return payload, digest

    def approve(self, case_id: str, session_id: str, snapshot_hash: str, accepted_notice: bool) -> ApprovalRecord:
        if not accepted_notice:
            raise ValidationFailed("persetujuan harus eksplisit")
        case = self.cases.get_owned(case_id, session_id)
        if case.state != State.WAITING_APPROVAL:
            raise ApprovalRequired("kasus belum menunggu persetujuan")
        assert_no_blocking_conflicts(self.conflicts.list_for_case(case_id))
        _, digest = self.current_snapshot(case_id)
        if digest != snapshot_hash:
            raise ApprovalHashMismatch("snapshot tidak cocok")
        existing = self.approvals.active_for_case(case_id)
        if existing is not None:
            existing.revoked_at = now_utc()
            existing.revoke_reason = "replaced"
            self.approvals.save(existing)
        scope = (
            ApprovalScope.PRE_BRIEF
            if case.route == Route.PRE_INCIDENT_CHECK
            else ApprovalScope.POST_CASE_PACK
        )
        record = ApprovalRecord(
            approval_id=new_id("appr"),
            case_id=case_id,
            actor="USER",
            scope=scope,
            snapshot_hash=digest,
            approved_at=now_utc(),
            notice_version=NOTICE_VERSION,
        )
        self.approvals.add(record)
        case.approved_snapshot_hash = digest
        self.cases.set_state(case, State.GENERATING, event_type="APPROVAL_GRANTED")
        return record

    def revoke(self, case_id: str, session_id: str, reason: str) -> None:
        case = self.cases.get_owned(case_id, session_id)
        active = self.approvals.active_for_case(case_id)
        if active is not None:
            active.revoked_at = now_utc()
            active.revoke_reason = reason
            self.approvals.save(active)
        case.approved_snapshot_hash = None
        if case.state in {State.WAITING_APPROVAL, State.GENERATING, State.VERIFYING, State.HANDOFF_READY}:
            self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="APPROVAL_REVOKED")
        else:
            self.cases.touch(case)
