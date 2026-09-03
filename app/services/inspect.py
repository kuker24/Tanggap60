from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.models import Criticality, EvidenceKind, EvidenceStatus, FactRecord, FactType, ReviewStatus
from app.domain.policies import sha256_text
from app.domain.states import State
from app.hermes.model import extract_with_model
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
from app.services.extraction import (
    NullOcr,
    OcrPort,
    PageText,
    decode_pages,
    encode_pages,
    excerpt_hash,
    extract_pdf_pages,
    extract_plain,
    iter_pdf_images,
    locator_for,
)
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
        ocr_total_ms = 0
        for item in items:
            data = self.storage.read_bytes(case_id, item.storage_key)
            pages: list[PageText] = []
            warning = None
            try:
                if item.kind in {EvidenceKind.TEXT, EvidenceKind.URL}:
                    pages = [PageText(page=1, text=extract_plain(data))]
                elif item.mime == "application/pdf":
                    pages = extract_pdf_pages(data)
                    if not any(page.text for page in pages):
                        by_page: dict[int, list[str]] = {}
                        boxes_by: dict[int, list[tuple[str, int, int, int, int]]] = {}
                        for page_no, image_bytes in iter_pdf_images(data):
                            try:
                                ocr_start = time.perf_counter()
                                text = self.ocr.recognize(image_bytes)
                                ocr_total_ms += int((time.perf_counter() - ocr_start) * 1000)
                            except Exception:
                                text = ""
                            if text:
                                by_page.setdefault(page_no, []).append(text)
                            recognize_boxes = getattr(self.ocr, "recognize_boxes", None)
                            if callable(recognize_boxes):
                                try:
                                    boxes_by.setdefault(page_no, []).extend(list(recognize_boxes(image_bytes)))
                                except Exception:
                                    pass
                        if by_page:
                            pages = [
                                PageText(
                                    page=num,
                                    text="\n".join(by_page[num]),
                                    boxes=boxes_by.get(num, []),
                                )
                                for num in sorted(by_page)
                            ]
                        else:
                            warning = "MANUAL_REVIEW_REQUIRED"
                else:
                    try:
                        ocr_start = time.perf_counter()
                        text = self.ocr.recognize(data)
                        ocr_total_ms += int((time.perf_counter() - ocr_start) * 1000)
                        boxes = []
                        recognize_boxes = getattr(self.ocr, "recognize_boxes", None)
                        if callable(recognize_boxes):
                            # recognize_boxes already cached, not timed doubly (cache hit 0ms)
                            boxes = list(recognize_boxes(data))
                        pages = [PageText(page=1, text=text, boxes=boxes)]
                    except Exception:
                        warning = "MANUAL_REVIEW_REQUIRED"
                        pages = [PageText(page=1, text="")]
            except Exception:
                warning = "MANUAL_REVIEW_REQUIRED"
                pages = [PageText(page=1, text="")]
            ref = new_id("txt")
            key = self.storage.new_key()
            stored = encode_pages(pages)
            self.storage.write_atomic(case_id, key, stored)
            text_all = "\n".join(page.text for page in pages)
            self.derived.add(ref, case_id, item.evidence_id, sha256_text(text_all), key)
            item.extracted_text_ref = ref
            item.status = EvidenceStatus.EXTRACTED
            item.warning = warning
            if pages:
                item.page_count = max(item.page_count, len(pages))
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
        return {"evidence": results, "ocr_total_ms": ocr_total_ms}

    def extract_candidate_facts(self, case_id: str) -> dict[str, object]:
        items = self.evidence.list_for_case(case_id)
        created = 0
        unauthorized_tool = False
        used_model = False
        model_total_ms = 0
        seen: set[tuple[str, str, str]] = {
            (f.type.value, f.normalized_value or f.raw_value, f.source_evidence_id)
            for f in self.facts.list_for_case(case_id)
        }
        for item in items:
            if not item.extracted_text_ref:
                continue
            derived = [d for d in self.derived.list_for_case(case_id) if d.ref == item.extracted_text_ref]
            if not derived:
                continue
            pages = decode_pages(self.storage.read_bytes(case_id, derived[0].storage_key))
            text_all = "\n".join(page.text for page in pages)
            lowered = text_all.lower()
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
                    source_bbox="p1:excerpt",
                    source_excerpt_hash=excerpt_hash(text_all[:80]),
                )
                self.facts.add(fact)
                created += 1
                continue
            m_start = time.perf_counter()
            extras = extract_with_model(text_all, self.settings)
            model_total_ms += int((time.perf_counter() - m_start) * 1000)
            if extras:
                used_model = True
            for page in pages:
                candidates = extract_candidates(page.text, page=page.page, boxes=page.boxes)
                if page.page == 1:
                    for extra in extras:
                        extra.page = 1
                        extra.locator = extra.locator or locator_for(extra.raw_value, 1, 0, min(len(extra.raw_value), 1), page.boxes)
                        candidates.append(extra)
                for candidate in candidates:
                    key = (
                        candidate.type.value,
                        candidate.normalized_value or candidate.raw_value,
                        item.evidence_id,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
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
                        source_page=candidate.page,
                        source_bbox=candidate.locator or f"p{candidate.page}:excerpt",
                        source_excerpt_hash=excerpt_hash(candidate.excerpt),
                    )
                    self.facts.add(fact)
                    created += 1
        if created == 0:
            case = self.case_repo.get(case_id)
            case.route_reason = "MANUAL_REVIEW_REQUIRED"
            self.cases.touch(case)
        return {"candidates": created, "unauthorized_tool": unauthorized_tool, "model_used": used_model, "model_total_ms": model_total_ms}

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
