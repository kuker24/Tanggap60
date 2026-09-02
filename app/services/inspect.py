from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.models import Criticality, EvidenceKind, EvidenceStatus, FactRecord, FactType, ReviewStatus
from app.domain.policies import sha256_text
from app.domain.states import State
from app.infrastructure.repositories import (
    ActionRepository,
    CaseRepository,
    ConflictRepository,
    DerivedTextRepository,
    EvidenceRepository,
    FactRepository,
    TransactionRepository,
)
from app.infrastructure.storage import CaseStorage
from app.services.cases import CaseService
from app.services.conflicts import detect_conflicts, missing_fields
from app.services.extraction import NullOcr, OcrPort, excerpt_hash, extract_pdf_text, extract_plain
from app.services.facts import extract_candidates
from app.services.ids import new_id
from app.services.plan import actions_for_route
from app.services.routing import apply_route, group_transactions, readiness_notes

INJECTION_MARKERS = (
    "ignore previous",
    "abaikan instruksi",
    "system prompt",
    "panggil tool",
    "call tool",
)


class InspectService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: CaseStorage,
        cases: CaseService,
        ocr: OcrPort | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.cases = cases
        self.ocr = ocr or NullOcr()
        self.evidence = EvidenceRepository(session)
        self.derived = DerivedTextRepository(session)
        self.facts = FactRepository(session)
        self.conflicts = ConflictRepository(session)
        self.actions = ActionRepository(session)
        self.txs = TransactionRepository(session)
        self.case_repo = CaseRepository(session)

    def inspect_evidence(self, case_id: str) -> dict[str, object]:
        items = self.evidence.list_for_case(case_id)
        results: list[dict[str, object]] = []
        for item in items:
            data = self.storage.read_bytes(case_id, item.storage_key)
            text = ""
            warning = None
            try:
                if item.kind in {EvidenceKind.TEXT, EvidenceKind.URL}:
                    text = extract_plain(data)
                elif item.mime == "application/pdf":
                    text = extract_pdf_text(data)
                    if not text:
                        warning = "MANUAL_REVIEW_REQUIRED"
                else:
                    try:
                        text = self.ocr.recognize(data)
                    except Exception:
                        warning = "MANUAL_REVIEW_REQUIRED"
                        text = ""
            except Exception:
                warning = "MANUAL_REVIEW_REQUIRED"
                text = ""
            ref = new_id("txt")
            key = self.storage.new_key()
            stored = text.encode("utf-8")
            self.storage.write_atomic(case_id, key, stored)
            self.derived.add(ref, case_id, item.evidence_id, sha256_text(text), key)
            item.extracted_text_ref = ref
            item.status = EvidenceStatus.EXTRACTED
            item.warning = warning
            self.evidence.save(item)
            results.append(
                {
                    "evidence_id": item.evidence_id,
                    "mime": item.mime,
                    "sha256": item.sha256,
                    "page_count": item.page_count,
                    "text_ref": ref,
                    "warning": warning,
                }
            )
        case = self.case_repo.get(case_id)
        if case.state == State.INGESTING:
            self.cases.set_state(case, State.EXTRACTING, event_type="INSPECT_DONE")
        return {"evidence": results}

    def extract_candidate_facts(self, case_id: str) -> dict[str, object]:
        items = self.evidence.list_for_case(case_id)
        created = 0
        unauthorized_tool = False
        for item in items:
            if not item.extracted_text_ref:
                continue
            derived = [d for d in self.derived.list_for_case(case_id) if d.ref == item.extracted_text_ref]
            if not derived:
                continue
            text = self.storage.read_bytes(case_id, derived[0].storage_key).decode("utf-8", errors="replace")
            lowered = text.lower()
            if any(marker in lowered for marker in INJECTION_MARKERS):
                fact = FactRecord(
                    fact_id=new_id("fact"),
                    case_id=case_id,
                    type=FactType.CLAIM,
                    raw_value="klaim tidak dipercaya sebagai instruksi",
                    normalized_value="untrusted_claim",
                    criticality=Criticality.OPTIONAL,
                    confidence=0.4,
                    review_status=ReviewStatus.CANDIDATE,
                    source_evidence_id=item.evidence_id,
                    source_page=1,
                    source_bbox="excerpt",
                    source_excerpt_hash=excerpt_hash(text[:80]),
                )
                self.facts.add(fact)
                created += 1
                continue
            for candidate in extract_candidates(text):
                fact = FactRecord(
                    fact_id=new_id("fact"),
                    case_id=case_id,
                    type=candidate.type,
                    raw_value=candidate.raw_value,
                    normalized_value=candidate.normalized_value,
                    criticality=candidate.criticality,
                    confidence=candidate.confidence,
                    review_status=ReviewStatus.CANDIDATE,
                    source_evidence_id=item.evidence_id,
                    source_page=1,
                    source_bbox="image region",
                    source_excerpt_hash=excerpt_hash(candidate.excerpt),
                )
                self.facts.add(fact)
                created += 1
        if created == 0:
            case = self.case_repo.get(case_id)
            case.route_reason = "MANUAL_REVIEW_REQUIRED"
            self.cases.touch(case)
        return {"candidates": created, "unauthorized_tool": unauthorized_tool}

    def validate_case_facts(self, case_id: str) -> dict[str, object]:
        facts = self.facts.list_for_case(case_id)
        self.conflicts.delete_for_case(case_id)
        detected = detect_conflicts(case_id, facts)
        for conflict in detected:
            self.conflicts.add(conflict)
        missing = missing_fields(facts)
        case = self.case_repo.get(case_id)
        route, reason, confidence, ask = apply_route(case.declared_condition, facts)
        case.route = route
        case.route_reason = reason
        case.route_confidence = confidence
        case.ask_loss_question = ask
        evidence = self.evidence.list_for_case(case_id)
        groups = group_transactions(case_id, facts, [e.evidence_id for e in evidence])
        self.txs.replace_for_case(case_id, groups)
        names = " ".join(e.original_name_display.lower() for e in evidence)
        notes = readiness_notes(groups, "chat" in names, "transfer" in names)
        if case.state == State.EXTRACTING:
            self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="REVIEW_REQUIRED")
        elif case.state == State.REVIEW_REQUIRED:
            from app.domain.policies import blocking_conflicts_open, critical_facts_reviewed

            if not blocking_conflicts_open(detected) and critical_facts_reviewed(facts):
                self.actions.replace_for_case(case_id, actions_for_route(case_id, case.route, facts))
                self.cases.set_state(case, State.READY_FOR_ACTION, event_type="READY_FOR_ACTION")
            else:
                self.cases.touch(case)
        else:
            self.cases.touch(case)
        return {
            "conflicts": len(detected),
            "missing": missing,
            "readiness_notes": notes,
            "route": case.route.value,
            "ask_loss_question": ask,
        }
