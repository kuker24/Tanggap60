from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.errors import NotFound, StaleCaseVersion
from app.domain.models import (
    ActionRecord,
    ApprovalRecord,
    ArtifactRecord,
    AuditEventRecord,
    CaseRecord,
    ConflictRecord,
    EvidenceRecord,
    FactRecord,
    ReceiptRecord,
    TransactionGroupRecord,
)
from app.domain.states import DeclaredCondition, Mode, Route, State
from app.infrastructure.db import (
    ActionRow,
    ApprovalRow,
    ArtifactRow,
    AuditEventRow,
    CaseRow,
    ConflictRow,
    DerivedTextRow,
    EvidenceRow,
    FactRow,
    IdempotencyRow,
    ReceiptRow,
    TransactionRow,
)


def _dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class CaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, case: CaseRecord) -> None:
        self.session.add(
            CaseRow(
                case_id=case.case_id,
                mode=case.mode.value,
                route=case.route.value,
                state=case.state.value,
                declared_condition=case.declared_condition.value,
                owner_session_id=case.owner_session_id,
                case_token_hash=case.case_token_hash,
                created_at=case.created_at,
                updated_at=case.updated_at,
                expires_at=case.expires_at,
                version=case.version,
                route_reason=case.route_reason,
                route_confidence=case.route_confidence,
                approved_snapshot_hash=case.approved_snapshot_hash,
                user_decision=case.user_decision,
                ask_loss_question=case.ask_loss_question,
            )
        )

    def get(self, case_id: str) -> CaseRecord:
        row = self.session.get(CaseRow, case_id)
        if row is None:
            raise NotFound("case not found")
        return self._to_model(row)

    def save(self, case: CaseRecord, expected_version: int | None = None) -> CaseRecord:
        row = self.session.get(CaseRow, case.case_id)
        if row is None:
            raise NotFound("case not found")
        if expected_version is not None and row.version != expected_version:
            raise StaleCaseVersion("case version stale")
        row.mode = case.mode.value
        row.route = case.route.value
        row.state = case.state.value
        row.declared_condition = case.declared_condition.value
        row.updated_at = case.updated_at
        row.expires_at = case.expires_at
        row.version = case.version
        row.route_reason = case.route_reason
        row.route_confidence = case.route_confidence
        row.approved_snapshot_hash = case.approved_snapshot_hash
        row.user_decision = case.user_decision
        row.ask_loss_question = case.ask_loss_question
        return case

    def bump(self, case: CaseRecord, expected_version: int | None = None) -> CaseRecord:
        if expected_version is not None and case.version != expected_version:
            raise StaleCaseVersion("case version stale")
        case.version += 1
        return self.save(case, expected_version=expected_version)

    def delete(self, case_id: str) -> None:
        self.session.execute(delete(CaseRow).where(CaseRow.case_id == case_id))

    def list_expired(self, now: datetime) -> list[CaseRecord]:
        rows = self.session.scalars(select(CaseRow).where(CaseRow.expires_at <= now)).all()
        return [self._to_model(r) for r in rows]

    def counts_by_state(self) -> dict[str, int]:
        rows = self.session.execute(select(CaseRow.state)).all()
        counts: dict[str, int] = {}
        for (state,) in rows:
            counts[state] = counts.get(state, 0) + 1
        return counts

    @staticmethod
    def _to_model(row: CaseRow) -> CaseRecord:
        return CaseRecord(
            case_id=row.case_id,
            mode=Mode(row.mode),
            route=Route(row.route),
            state=State(row.state),
            declared_condition=DeclaredCondition(row.declared_condition),
            owner_session_id=row.owner_session_id,
            case_token_hash=row.case_token_hash,
            created_at=_dt(row.created_at),  # type: ignore[arg-type]
            updated_at=_dt(row.updated_at),  # type: ignore[arg-type]
            expires_at=_dt(row.expires_at),  # type: ignore[arg-type]
            version=row.version,
            route_reason=row.route_reason,
            route_confidence=row.route_confidence,
            approved_snapshot_hash=row.approved_snapshot_hash,
            user_decision=row.user_decision,
            ask_loss_question=row.ask_loss_question,
        )


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, item: EvidenceRecord) -> None:
        self.session.add(
            EvidenceRow(
                evidence_id=item.evidence_id,
                case_id=item.case_id,
                kind=item.kind.value,
                original_name_display=item.original_name_display,
                storage_key=item.storage_key,
                mime=item.mime,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                page_count=item.page_count,
                status=item.status.value,
                retention_until=item.retention_until,
                extracted_text_ref=item.extracted_text_ref,
                warning=item.warning,
            )
        )

    def list_for_case(self, case_id: str) -> list[EvidenceRecord]:
        rows = self.session.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case_id)).all()
        return [self._to_model(r) for r in rows]

    def get(self, evidence_id: str) -> EvidenceRecord:
        row = self.session.get(EvidenceRow, evidence_id)
        if row is None:
            raise NotFound("evidence not found")
        return self._to_model(row)

    def save(self, item: EvidenceRecord) -> None:
        row = self.session.get(EvidenceRow, item.evidence_id)
        if row is None:
            raise NotFound("evidence not found")
        row.status = item.status.value
        row.extracted_text_ref = item.extracted_text_ref
        row.warning = item.warning
        row.page_count = item.page_count

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(EvidenceRow).where(EvidenceRow.case_id == case_id))

    def delete(self, evidence_id: str) -> None:
        self.session.execute(delete(EvidenceRow).where(EvidenceRow.evidence_id == evidence_id))

    @staticmethod
    def _to_model(row: EvidenceRow) -> EvidenceRecord:
        from app.domain.models import EvidenceKind, EvidenceStatus

        return EvidenceRecord(
            evidence_id=row.evidence_id,
            case_id=row.case_id,
            kind=EvidenceKind(row.kind),
            original_name_display=row.original_name_display,
            storage_key=row.storage_key,
            mime=row.mime,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            page_count=row.page_count,
            status=EvidenceStatus(row.status),
            retention_until=_dt(row.retention_until),  # type: ignore[arg-type]
            extracted_text_ref=row.extracted_text_ref,
            warning=row.warning,
        )


class FactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, fact: FactRecord) -> None:
        self.session.add(
            FactRow(
                fact_id=fact.fact_id,
                case_id=fact.case_id,
                type=fact.type.value,
                raw_value=fact.raw_value,
                normalized_value=fact.normalized_value,
                criticality=fact.criticality.value,
                confidence=fact.confidence,
                review_status=fact.review_status.value,
                source_evidence_id=fact.source_evidence_id,
                source_page=fact.source_page,
                source_bbox=fact.source_bbox,
                source_excerpt_hash=fact.source_excerpt_hash,
                corrected_from_fact_id=fact.corrected_from_fact_id,
            )
        )

    def list_for_case(self, case_id: str) -> list[FactRecord]:
        rows = self.session.scalars(select(FactRow).where(FactRow.case_id == case_id)).all()
        return [self._to_model(r) for r in rows]

    def get(self, fact_id: str) -> FactRecord:
        row = self.session.get(FactRow, fact_id)
        if row is None:
            raise NotFound("fact not found")
        return self._to_model(row)

    def save(self, fact: FactRecord) -> None:
        row = self.session.get(FactRow, fact.fact_id)
        if row is None:
            raise NotFound("fact not found")
        row.raw_value = fact.raw_value
        row.normalized_value = fact.normalized_value
        row.review_status = fact.review_status.value
        row.corrected_from_fact_id = fact.corrected_from_fact_id

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(FactRow).where(FactRow.case_id == case_id))

    @staticmethod
    def _to_model(row: FactRow) -> FactRecord:
        from app.domain.models import Criticality, FactType, ReviewStatus

        return FactRecord(
            fact_id=row.fact_id,
            case_id=row.case_id,
            type=FactType(row.type),
            raw_value=row.raw_value,
            normalized_value=row.normalized_value,
            criticality=Criticality(row.criticality),
            confidence=row.confidence,
            review_status=ReviewStatus(row.review_status),
            source_evidence_id=row.source_evidence_id,
            source_page=row.source_page,
            source_bbox=row.source_bbox,
            source_excerpt_hash=row.source_excerpt_hash,
            corrected_from_fact_id=row.corrected_from_fact_id,
        )


class ConflictRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, conflict: ConflictRecord) -> None:
        self.session.add(
            ConflictRow(
                conflict_id=conflict.conflict_id,
                case_id=conflict.case_id,
                type=conflict.type.value,
                fact_ids_json=json.dumps(conflict.fact_ids),
                severity=conflict.severity.value,
                status=conflict.status.value,
                resolution_fact_id=conflict.resolution_fact_id,
                resolved_by=conflict.resolved_by,
                resolved_at=conflict.resolved_at,
            )
        )

    def list_for_case(self, case_id: str) -> list[ConflictRecord]:
        rows = self.session.scalars(select(ConflictRow).where(ConflictRow.case_id == case_id)).all()
        return [self._to_model(r) for r in rows]

    def get(self, conflict_id: str) -> ConflictRecord:
        row = self.session.get(ConflictRow, conflict_id)
        if row is None:
            raise NotFound("conflict not found")
        return self._to_model(row)

    def save(self, conflict: ConflictRecord) -> None:
        row = self.session.get(ConflictRow, conflict.conflict_id)
        if row is None:
            raise NotFound("conflict not found")
        row.status = conflict.status.value
        row.resolution_fact_id = conflict.resolution_fact_id
        row.resolved_by = conflict.resolved_by
        row.resolved_at = conflict.resolved_at

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(ConflictRow).where(ConflictRow.case_id == case_id))

    @staticmethod
    def _to_model(row: ConflictRow) -> ConflictRecord:
        from app.domain.models import ConflictSeverity, ConflictStatus, ConflictType

        return ConflictRecord(
            conflict_id=row.conflict_id,
            case_id=row.case_id,
            type=ConflictType(row.type),
            fact_ids=json.loads(row.fact_ids_json),
            severity=ConflictSeverity(row.severity),
            status=ConflictStatus(row.status),
            resolution_fact_id=row.resolution_fact_id,
            resolved_by=row.resolved_by,
            resolved_at=_dt(row.resolved_at) if row.resolved_at else None,  # type: ignore[arg-type]
        )


class ActionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_case(self, case_id: str, actions: list[ActionRecord]) -> None:
        self.session.execute(delete(ActionRow).where(ActionRow.case_id == case_id))
        for action in actions:
            self.session.add(
                ActionRow(
                    action_id=action.action_id,
                    case_id=action.case_id,
                    priority=action.priority.value,
                    channel=action.channel.value,
                    instruction=action.instruction,
                    status=action.status.value,
                    official_url_key=action.official_url_key,
                    requires_external_user_action=action.requires_external_user_action,
                )
            )

    def list_for_case(self, case_id: str) -> list[ActionRecord]:
        rows = self.session.scalars(select(ActionRow).where(ActionRow.case_id == case_id)).all()
        from app.domain.models import ActionChannel, ActionPriority, ActionStatus

        return [
            ActionRecord(
                action_id=r.action_id,
                case_id=r.case_id,
                priority=ActionPriority(r.priority),
                channel=ActionChannel(r.channel),
                instruction=r.instruction,
                status=ActionStatus(r.status),
                official_url_key=r.official_url_key,
                requires_external_user_action=r.requires_external_user_action,
            )
            for r in rows
        ]

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(ActionRow).where(ActionRow.case_id == case_id))


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_case(self, case_id: str, groups: list[TransactionGroupRecord]) -> None:
        self.session.execute(delete(TransactionRow).where(TransactionRow.case_id == case_id))
        for group in groups:
            self.session.add(
                TransactionRow(
                    transaction_group_id=group.transaction_group_id,
                    case_id=group.case_id,
                    victim_account=group.victim_account,
                    destination_account=group.destination_account,
                    amount=group.amount,
                    transferred_at=group.transferred_at,
                    evidence_ids_json=json.dumps(group.evidence_ids),
                    readiness=group.readiness,
                )
            )

    def list_for_case(self, case_id: str) -> list[TransactionGroupRecord]:
        rows = self.session.scalars(
            select(TransactionRow).where(TransactionRow.case_id == case_id)
        ).all()
        return [
            TransactionGroupRecord(
                transaction_group_id=r.transaction_group_id,
                case_id=r.case_id,
                victim_account=r.victim_account,
                destination_account=r.destination_account,
                amount=r.amount,
                transferred_at=r.transferred_at,
                evidence_ids=json.loads(r.evidence_ids_json),
                readiness=r.readiness,
            )
            for r in rows
        ]

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(TransactionRow).where(TransactionRow.case_id == case_id))


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, approval: ApprovalRecord) -> None:
        self.session.add(
            ApprovalRow(
                approval_id=approval.approval_id,
                case_id=approval.case_id,
                actor=approval.actor,
                scope=approval.scope.value,
                snapshot_hash=approval.snapshot_hash,
                approved_at=approval.approved_at,
                notice_version=approval.notice_version,
                revoked_at=approval.revoked_at,
                revoke_reason=approval.revoke_reason,
            )
        )

    def active_for_case(self, case_id: str) -> ApprovalRecord | None:
        rows = self.session.scalars(
            select(ApprovalRow).where(ApprovalRow.case_id == case_id, ApprovalRow.revoked_at.is_(None))
        ).all()
        if not rows:
            return None
        return self._to_model(rows[-1])

    def save(self, approval: ApprovalRecord) -> None:
        row = self.session.get(ApprovalRow, approval.approval_id)
        if row is None:
            raise NotFound("approval not found")
        row.revoked_at = approval.revoked_at
        row.revoke_reason = approval.revoke_reason

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(ApprovalRow).where(ApprovalRow.case_id == case_id))

    @staticmethod
    def _to_model(row: ApprovalRow) -> ApprovalRecord:
        from app.domain.models import ApprovalScope

        return ApprovalRecord(
            approval_id=row.approval_id,
            case_id=row.case_id,
            actor=row.actor,
            scope=ApprovalScope(row.scope),
            snapshot_hash=row.snapshot_hash,
            approved_at=_dt(row.approved_at),  # type: ignore[arg-type]
            notice_version=row.notice_version,
            revoked_at=_dt(row.revoked_at) if row.revoked_at else None,  # type: ignore[arg-type]
            revoke_reason=row.revoke_reason,
        )


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, artifact: ArtifactRecord) -> None:
        self.session.add(
            ArtifactRow(
                artifact_id=artifact.artifact_id,
                case_id=artifact.case_id,
                type=artifact.type.value,
                storage_key=artifact.storage_key,
                mime=artifact.mime,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                source_snapshot_hash=artifact.source_snapshot_hash,
                verify_status=artifact.verify_status.value,
                verify_details_json=json.dumps(artifact.verify_details),
            )
        )

    def list_for_case(self, case_id: str) -> list[ArtifactRecord]:
        rows = self.session.scalars(select(ArtifactRow).where(ArtifactRow.case_id == case_id)).all()
        return [self._to_model(r) for r in rows]

    def get(self, artifact_id: str) -> ArtifactRecord:
        row = self.session.get(ArtifactRow, artifact_id)
        if row is None:
            raise NotFound("artifact not found")
        return self._to_model(row)

    def save(self, artifact: ArtifactRecord) -> None:
        row = self.session.get(ArtifactRow, artifact.artifact_id)
        if row is None:
            raise NotFound("artifact not found")
        row.verify_status = artifact.verify_status.value
        row.verify_details_json = json.dumps(artifact.verify_details)

    def find_by_type(self, case_id: str, artifact_type: str) -> ArtifactRecord | None:
        row = self.session.scalar(
            select(ArtifactRow).where(
                ArtifactRow.case_id == case_id, ArtifactRow.type == artifact_type
            )
        )
        return self._to_model(row) if row else None

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(ArtifactRow).where(ArtifactRow.case_id == case_id))

    @staticmethod
    def _to_model(row: ArtifactRow) -> ArtifactRecord:
        from app.domain.models import ArtifactType, VerifyStatus

        return ArtifactRecord(
            artifact_id=row.artifact_id,
            case_id=row.case_id,
            type=ArtifactType(row.type),
            storage_key=row.storage_key,
            mime=row.mime,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            source_snapshot_hash=row.source_snapshot_hash,
            verify_status=VerifyStatus(row.verify_status),
            verify_details=json.loads(row.verify_details_json or "{}"),
        )


class ReceiptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, receipt: ReceiptRecord, ticket_normalized: str) -> None:
        self.session.add(
            ReceiptRow(
                receipt_id=receipt.receipt_id,
                case_id=receipt.case_id,
                ticket_value_masked=receipt.ticket_value_masked,
                source=receipt.source.value,
                format_status=receipt.format_status.value,
                local_match_status=receipt.local_match_status.value,
                official_status="NOT_VERIFIED",
                recorded_at=receipt.recorded_at,
                receipt_evidence_id=receipt.receipt_evidence_id,
                ticket_normalized=ticket_normalized,
            )
        )

    def get_for_case(self, case_id: str) -> ReceiptRecord | None:
        row = self.session.scalar(select(ReceiptRow).where(ReceiptRow.case_id == case_id))
        if row is None:
            return None
        from app.domain.models import FormatStatus, LocalMatchStatus, ReceiptSource

        return ReceiptRecord(
            receipt_id=row.receipt_id,
            case_id=row.case_id,
            ticket_value_masked=row.ticket_value_masked,
            source=ReceiptSource(row.source),
            format_status=FormatStatus(row.format_status),
            local_match_status=LocalMatchStatus(row.local_match_status),
            official_status="NOT_VERIFIED",
            recorded_at=_dt(row.recorded_at),  # type: ignore[arg-type]
            receipt_evidence_id=row.receipt_evidence_id,
        )

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(ReceiptRow).where(ReceiptRow.case_id == case_id))


class EventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: AuditEventRecord) -> None:
        self.session.add(
            AuditEventRow(
                event_id=event.event_id,
                case_id=event.case_id,
                run_id=event.run_id,
                event_type=event.event_type,
                state_before=event.state_before,
                state_after=event.state_after,
                tool_name=event.tool_name,
                tool_version=event.tool_version,
                duration_ms=event.duration_ms,
                result_code=event.result_code,
                error_code=event.error_code,
                payload_hash=event.payload_hash,
                created_at=event.created_at,
                planner=event.planner,
                execution=event.execution,
            )
        )

    def list_for_case(self, case_id: str) -> list[AuditEventRecord]:
        rows = self.session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.case_id == case_id)
            .order_by(AuditEventRow.created_at)
        ).all()
        return [
            AuditEventRecord(
                event_id=r.event_id,
                case_id=r.case_id,
                run_id=r.run_id,
                event_type=r.event_type,
                state_before=r.state_before,
                state_after=r.state_after,
                tool_name=r.tool_name,
                tool_version=r.tool_version,
                duration_ms=r.duration_ms,
                result_code=r.result_code,
                error_code=r.error_code,
                payload_hash=r.payload_hash,
                created_at=_dt(r.created_at),  # type: ignore[arg-type]
                planner=r.planner,
                execution=r.execution,
            )
            for r in rows
        ]

    def delete_content_for_case(self, case_id: str) -> None:
        self.session.execute(
            update(AuditEventRow)
            .where(AuditEventRow.case_id == case_id)
            .values(case_id="purged", payload_hash=None)
        )


class DerivedTextRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, ref: str, case_id: str, evidence_id: str, sha256: str, storage_key: str) -> None:
        self.session.add(
            DerivedTextRow(
                ref=ref,
                case_id=case_id,
                evidence_id=evidence_id,
                sha256=sha256,
                storage_key=storage_key,
            )
        )

    def list_for_case(self, case_id: str) -> list[DerivedTextRow]:
        return list(self.session.scalars(select(DerivedTextRow).where(DerivedTextRow.case_id == case_id)))

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(DerivedTextRow).where(DerivedTextRow.case_id == case_id))


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str) -> IdempotencyRow | None:
        return self.session.get(IdempotencyRow, key)

    def add(self, key: str, case_id: str, payload_hash: str, response_json: str, created_at: datetime) -> None:
        self.session.add(
            IdempotencyRow(
                key=key,
                case_id=case_id,
                payload_hash=payload_hash,
                response_json=response_json,
                created_at=created_at,
            )
        )

    def delete_for_case(self, case_id: str) -> None:
        self.session.execute(delete(IdempotencyRow).where(IdempotencyRow.case_id == case_id))
