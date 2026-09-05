from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.errors import NotFound, ValidationFailed
from app.domain.models import ConflictStatus, Criticality, FactRecord, FactType, ReviewStatus
from app.domain.policies import normalize_amount, normalize_datetime, sha256_text
from app.domain.states import State
from app.infrastructure.repositories import ConflictRepository, FactRepository
from app.services.approval import ApprovalService
from app.services.cases import CaseService, now_utc
from app.services.ids import new_id


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
                norm = normalize_amount(value)
                if not norm:
                    raise ValidationFailed("format jumlah tidak dikenali")
                fact.normalized_value = norm
            elif fact.type.value == "DATETIME":
                fact.normalized_value = normalize_datetime(value) or value
            else:
                fact.normalized_value = value
            fact.review_status = ReviewStatus.CORRECTED
            fact.corrected_from_fact_id = fact.fact_id
        else:
            raise ValidationFailed("aksi tidak dikenal")
        self.facts.save(fact)
        if case.state in {State.WAITING_APPROVAL, State.GENERATING, State.VERIFYING, State.HANDOFF_READY}:
            try:
                from app.infrastructure.repositories import EvidenceRepository, UnitMappingRepository
                from app.services.reporting_units import compile_reporting_units

                all_facts = self.facts.list_for_case(case_id)
                evidence = EvidenceRepository(self.session).list_for_case(case_id)
                mappings = UnitMappingRepository(self.session).list_for_case(case_id)
                decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
                units = compile_reporting_units(case_id, all_facts, evidence, decs if decs else None)
                affected_unit_ids = {u.unit_id for u in units if fact.fact_id in u.fact_ids}
                approvals = self.approval.approvals.list_for_case(case_id)
                revoked_any = False
                for appr in list(approvals):
                    if appr.revoked_at is not None:
                        continue
                    should_revoke = False
                    if appr.target_id is None:
                        should_revoke = True
                    else:
                        if not affected_unit_ids:
                            should_revoke = False  # fact not in any unit -> don't revoke unit approvals (isolated)
                        elif appr.target_id in affected_unit_ids:
                            should_revoke = True
                    if should_revoke:
                        appr.revoked_at = now_utc()
                        appr.revoke_reason = "fact changed for unit"
                        self.approval.approvals.save(appr)
                        revoked_any = True
                remaining = [a for a in self.approval.approvals.list_for_case(case_id) if a.revoked_at is None]
                if revoked_any:
                    case.approved_snapshot_hash = remaining[0].snapshot_hash if remaining else None
                    if not remaining:
                        self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="APPROVAL_REVOKED")
                    elif any(a.target_id is not None for a in remaining):
                        # keep other unit approvals, don't force REVIEW_REQUIRED
                        self.cases.touch(case)
                    else:
                        self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="APPROVAL_REVOKED")
                else:
                    self.cases.touch(case)
            except Exception:
                self.approval.revoke(case_id, session_id, "fact changed")
        else:
            self.cases.touch(case)
        return fact

    def add_manual_fact(
        self,
        case_id: str,
        session_id: str,
        fact_type: str,
        value: str,
        expected_version: int,
    ) -> FactRecord:
        case = self.cases.get_owned(case_id, session_id)
        if case.version != expected_version:
            from app.domain.errors import StaleCaseVersion

            raise StaleCaseVersion("versi kasus usang")
        try:
            ftype = FactType(fact_type)
        except ValueError as exc:
            raise ValidationFailed("tipe data tidak dikenal") from exc
        raw = value.strip()
        if not raw:
            raise ValidationFailed("nilai wajib diisi")
        if ftype == FactType.AMOUNT:
            norm = normalize_amount(raw)
            if not norm:
                raise ValidationFailed("format jumlah tidak dikenali")
        elif ftype == FactType.DATETIME:
            norm = normalize_datetime(raw) or raw
        else:
            norm = raw
        fact = FactRecord(
            fact_id=new_id("fact"),
            case_id=case_id,
            type=ftype,
            raw_value=raw,
            normalized_value=norm,
            criticality=Criticality.CRITICAL if ftype.value in {"AMOUNT", "ACCOUNT", "DATETIME"} else Criticality.IMPORTANT,
            confidence=1.0,
            review_status=ReviewStatus.CORRECTED,
            source_evidence_id="user-entered",
            source_page=None,
            source_bbox="USER_ENTERED",
            source_excerpt_hash=sha256_text(raw),
        )
        self.facts.add(fact)
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
        if conflict.case_id != case_id:
            raise NotFound("conflict not found")
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
            try:
                from app.infrastructure.repositories import EvidenceRepository, UnitMappingRepository
                from app.services.reporting_units import compile_reporting_units

                all_facts = self.facts.list_for_case(case_id)
                evidence = EvidenceRepository(self.session).list_for_case(case_id)
                mappings = UnitMappingRepository(self.session).list_for_case(case_id)
                decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
                units = compile_reporting_units(case_id, all_facts, evidence, decs if decs else None)
                affected_unit_ids = set()
                for u in units:
                    if set(conflict.fact_ids) & set(u.fact_ids):
                        affected_unit_ids.add(u.unit_id)
                    if conflict.resolution_fact_id and conflict.resolution_fact_id in u.fact_ids:
                        affected_unit_ids.add(u.unit_id)
                # If conflict involves facts not in any unit (global), affect all
                if not affected_unit_ids:
                    self.approval.revoke(case_id, session_id, "conflict resolved")
                else:
                    approvals = self.approval.approvals.list_for_case(case_id)
                    revoked_any = False
                    for appr in approvals:
                        if appr.revoked_at is not None:
                            continue
                        if appr.target_id is None:
                            appr.revoked_at = now_utc()
                            appr.revoke_reason = "conflict resolved"
                            self.approval.approvals.save(appr)
                            revoked_any = True
                        elif appr.target_id in affected_unit_ids:
                            appr.revoked_at = now_utc()
                            appr.revoke_reason = "conflict resolved for unit"
                            self.approval.approvals.save(appr)
                            revoked_any = True
                    remaining = [a for a in self.approval.approvals.list_for_case(case_id) if a.revoked_at is None]
                    if revoked_any:
                        case.approved_snapshot_hash = remaining[0].snapshot_hash if remaining else None
                        if not remaining:
                            self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="APPROVAL_REVOKED_CONFLICT")
                        elif any(a.target_id is not None for a in remaining):
                            self.cases.touch(case)
                        else:
                            self.cases.set_state(case, State.REVIEW_REQUIRED, event_type="APPROVAL_REVOKED_CONFLICT")
                    else:
                        self.cases.touch(case)
            except Exception:
                self.approval.revoke(case_id, session_id, "conflict resolved")
        else:
            self.cases.touch(case)
