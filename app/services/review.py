from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.errors import NotFound, ValidationFailed
from app.domain.models import ConflictStatus, FactRecord, ReviewStatus
from app.domain.policies import normalize_amount
from app.domain.states import State
from app.infrastructure.repositories import ConflictRepository, FactRepository
from app.services.approval import ApprovalService
from app.services.cases import CaseService, now_utc


class ReviewService:
    def __init__(self, session: Session, cases: CaseService, approval: ApprovalService) -> None:
        self.session = session
        self.cases = cases
        self.approval = approval
        self.facts = FactRepository(session)
        self.conflicts = ConflictRepository(session)

    def patch_fact(
        self,
        case_id: str,
        session_id: str,
        fact_id: str,
        action: str,
        value: str | None,
        expected_version: int,
    ) -> FactRecord:
        case = self.cases.get_owned(case_id, session_id)
        if case.version != expected_version:
            from app.domain.errors import StaleCaseVersion

            raise StaleCaseVersion("versi kasus usang")
        fact = self.facts.get(fact_id)
        if fact.case_id != case_id:
            raise NotFound("fact not found")
        if action == "confirm":
            fact.review_status = ReviewStatus.CONFIRMED
        elif action == "reject":
            fact.review_status = ReviewStatus.REJECTED
        elif action == "unavailable":
            fact.review_status = ReviewStatus.UNAVAILABLE
        elif action == "correct":
            if not value:
                raise ValidationFailed("nilai koreksi wajib")
            fact.raw_value = value
            if fact.type.value == "AMOUNT":
                fact.normalized_value = normalize_amount(value)
            else:
                fact.normalized_value = value
            fact.review_status = ReviewStatus.CORRECTED
            fact.corrected_from_fact_id = fact.fact_id
        else:
            raise ValidationFailed("aksi tidak dikenal")
        self.facts.save(fact)
        if case.state in {State.WAITING_APPROVAL, State.GENERATING, State.VERIFYING, State.HANDOFF_READY}:
            self.approval.revoke(case_id, session_id, "fact changed")
        else:
            self.cases.touch(case)
        return fact

    def resolve_conflict(
        self,
        case_id: str,
        session_id: str,
        conflict_id: str,
        resolution_fact_id: str,
        expected_version: int,
    ) -> None:
        case = self.cases.get_owned(case_id, session_id)
        if case.version != expected_version:
            from app.domain.errors import StaleCaseVersion

            raise StaleCaseVersion("versi kasus usang")
        conflict = self.conflicts.get(conflict_id)
        if resolution_fact_id not in conflict.fact_ids:
            raise ValidationFailed("fakta resolusi tidak terkait")
        for fact_id in conflict.fact_ids:
            fact = self.facts.get(fact_id)
            if fact.fact_id == resolution_fact_id:
                if fact.review_status == ReviewStatus.CANDIDATE:
                    fact.review_status = ReviewStatus.CONFIRMED
                    self.facts.save(fact)
            else:
                fact.review_status = ReviewStatus.REJECTED
                self.facts.save(fact)
        conflict.status = ConflictStatus.RESOLVED
        conflict.resolution_fact_id = resolution_fact_id
        conflict.resolved_by = "USER"
        conflict.resolved_at = now_utc()
        self.conflicts.save(conflict)
        if case.state in {State.WAITING_APPROVAL, State.READY_FOR_ACTION}:
            self.approval.revoke(case_id, session_id, "conflict resolved")
        else:
            self.cases.touch(case)
