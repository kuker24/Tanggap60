from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.errors import ValidationFailed
from app.domain.models import (
    FormatStatus,
    LocalMatchStatus,
    ReceiptRecord,
    ReceiptSource,
)
from app.domain.policies import mask_ticket, normalize_ticket, ticket_plausible
from app.domain.states import State
from app.infrastructure.repositories import ReceiptRepository
from app.infrastructure.storage import CaseStorage
from app.services.cases import CaseService, now_utc
from app.services.ids import new_id


class ReceiptService:
    def __init__(self, session: Session, cases: CaseService, storage: CaseStorage) -> None:
        self.session = session
        self.cases = cases
        self.storage = storage
        self.receipts = ReceiptRepository(session)

    def record(
        self,
        case_id: str,
        session_id: str,
        ticket_text: str | None,
        ocr_text: str | None,
        evidence_id: str | None,
        user_confirms_unreadable: bool = False,
        replace: bool = False,
    ) -> ReceiptRecord:
        case = self.cases.get_owned(case_id, session_id)
        if case.state not in {State.HANDOFF_READY, State.RECEIPT_RECORDED}:
            raise ValidationFailed("receipt hanya setelah handoff siap")
        existing = self.receipts.get_for_case(case_id)
        if existing is not None and not replace:
            return existing
        source = ReceiptSource.USER_INPUT
        if ticket_text and ocr_text:
            source = ReceiptSource.BOTH
        elif ocr_text and not ticket_text:
            source = ReceiptSource.RECEIPT_OCR
        text = ticket_text or ocr_text or ""
        if not text:
            raise ValidationFailed("isi nomor tiket atau unggah receipt")
        if source == ReceiptSource.RECEIPT_OCR and not ticket_plausible(text) and not user_confirms_unreadable:
            raise ValidationFailed("OCR tidak terbaca; konfirmasi manual diperlukan")
        if ticket_plausible(text):
            format_status = FormatStatus.PLAUSIBLE
        elif user_confirms_unreadable:
            format_status = FormatStatus.NOT_CHECKED
        else:
            format_status = FormatStatus.UNRECOGNIZED
        match = LocalMatchStatus.NOT_APPLICABLE
        if ticket_text and ocr_text:
            if normalize_ticket(ticket_text) == normalize_ticket(ocr_text):
                match = LocalMatchStatus.MATCH
            else:
                match = LocalMatchStatus.MISMATCH
        if existing is not None and replace:
            self.receipts.delete_for_case(case_id)
        record = ReceiptRecord(
            receipt_id=new_id("rcpt"),
            case_id=case_id,
            ticket_value_masked=mask_ticket(text),
            source=source,
            format_status=format_status,
            local_match_status=match,
            official_status="NOT_VERIFIED",
            recorded_at=now_utc(),
            receipt_evidence_id=evidence_id,
        )
        self.receipts.add(record, "")
        if match != LocalMatchStatus.MISMATCH and case.state == State.HANDOFF_READY:
            self.cases.set_state(case, State.RECEIPT_RECORDED, event_type="RECEIPT_RECORDED_BY_USER")
        return record
