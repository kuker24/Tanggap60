from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.errors import ValidationFailed
from app.domain.states import State
from app.infrastructure.jobs import JobQueue
from app.infrastructure.repositories import (
    ActionRepository,
    ApprovalRepository,
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    DerivedTextRepository,
    EventRepository,
    EvidenceRepository,
    FactRepository,
    IdempotencyRepository,
    ReceiptRepository,
    TransactionRepository,
)
from app.infrastructure.storage import CaseStorage
from app.services.cases import CaseService, now_utc


class PurgeService:
    def __init__(self, session: Session, cases: CaseService, storage: CaseStorage) -> None:
        self.session = session
        self.cases = cases
        self.storage = storage
        self.case_repo = CaseRepository(session)
        self.evidence = EvidenceRepository(session)
        self.facts = FactRepository(session)
        self.conflicts = ConflictRepository(session)
        self.actions = ActionRepository(session)
        self.txs = TransactionRepository(session)
        self.approvals = ApprovalRepository(session)
        self.artifacts = ArtifactRepository(session)
        self.receipts = ReceiptRepository(session)
        self.derived = DerivedTextRepository(session)
        self.events = EventRepository(session)
        self.jobs = JobQueue(session)
        self.idem = IdempotencyRepository(session)

    def purge(self, case_id: str, session_id: str, confirmation: str) -> dict[str, str]:
        if confirmation != "PURGE":
            raise ValidationFailed("konfirmasi purge tidak cocok")
        case = self.cases.get_owned(case_id, session_id)
        self._wipe(case.case_id)
        case.state = State.PURGED
        case.approved_snapshot_hash = None
        self.case_repo.delete(case.case_id)
        return {"status": "PURGED", "case_id": case_id}

    def purge_expired(self) -> int:
        count = 0
        for case in self.case_repo.list_expired(now_utc()):
            self._wipe(case.case_id)
            self.case_repo.delete(case.case_id)
            count += 1
        return count

    def _wipe(self, case_id: str) -> None:
        self.storage.purge_case(case_id)
        self.derived.delete_for_case(case_id)
        self.evidence.delete_for_case(case_id)
        self.facts.delete_for_case(case_id)
        self.conflicts.delete_for_case(case_id)
        self.actions.delete_for_case(case_id)
        self.txs.delete_for_case(case_id)
        self.approvals.delete_for_case(case_id)
        self.artifacts.delete_for_case(case_id)
        self.receipts.delete_for_case(case_id)
        self.jobs.delete_for_case(case_id)
        self.idem.delete_for_case(case_id)
        self.events.delete_content_for_case(case_id)
