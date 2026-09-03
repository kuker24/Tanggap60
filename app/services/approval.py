from __future__ import annotations

from typing import Any

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
    EvidenceRepository,
    FactRepository,
    TransactionRepository,
)
from app.services.cases import CaseService, now_utc
from app.services.ids import new_id
from app.services.readiness import assess, snapshot_readiness, snapshot_units


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
        self.evidence = EvidenceRepository(session)
        self.transactions = TransactionRepository(session)

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
        # compute reporting units and per-unit readiness for 2.2 snapshot — only for multi-unit or non-complete
        try:
            from app.infrastructure.repositories import UnitMappingRepository
            from app.services.reporting_units import compile_reporting_units

            raw_facts = self.facts.list_for_case(case_id)
            raw_evidence = self.evidence.list_for_case(case_id)
            raw_conflicts = self.conflicts.list_for_case(case_id)
            mappings = UnitMappingRepository(self.session).list_for_case(case_id)
            decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
            units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
            # only use 2.2 for multi-unit or incomplete/ambiguous; keep single complete as 2.1 for backward compat
            should_use_22 = bool(units) and (len(units) > 1 or any(getattr(u, "mapping_status", None) != "COMPLETE" for u in units))
            if not should_use_22:
                units_snapshot = None
                next_action_payload = None
                units = []
            else:
                from app.services.readiness import assess_units as _assess_units

                units_report = _assess_units(
                    case_id=case_id, units=units, facts=raw_facts, evidence=raw_evidence, conflicts=raw_conflicts, route=case.route
                )
                units_snapshot = snapshot_units(units_report)
                from app.services.next_action import next_action_to_dict as _natd
                from app.services.next_action import recommend_next_action as _recommend

                action_units = units
                next_act = _recommend(
                    case_id=case_id,
                    units=action_units,
                    conflicts=raw_conflicts,
                    readiness_by_unit=units_report.get("readiness_by_unit"),
                    incident_police_ready=(units_report.get("incident_police", {}).get("status") == "READY"),
                )
                next_action_payload = _natd(next_act)
        except Exception:
            units_snapshot = None
            next_action_payload = None
            units = []

        report = assess(
            case_id=case_id,
            route=case.route,
            facts=self.facts.list_for_case(case_id),
            conflicts=self.conflicts.list_for_case(case_id),
            evidence=self.evidence.list_for_case(case_id),
            transactions=self.transactions.list_for_case(case_id),
        )
        payload = snapshot_payload(
            facts=facts,
            conflicts=conflicts,
            route=case.route.value,
            actions=actions,
            notice_version=NOTICE_VERSION,
            template_version=TEMPLATE_VERSION,
            readiness=snapshot_readiness(report),
        )
        # additive 2.2 fields
        if units_snapshot is not None:
            payload["reporting_units_snapshot"] = units_snapshot
            payload["units"] = [
                {
                    "unit_id": u.unit_id,
                    "mapping_status": str(u.mapping_status.value if hasattr(u.mapping_status, "value") else u.mapping_status),
                    "fact_ids": sorted(u.fact_ids),
                    "evidence_ids": sorted(u.evidence_ids),
                }
                for u in sorted(units, key=lambda x: x.unit_id)
            ]
        if next_action_payload is not None:
            payload["next_best_action"] = next_action_payload
        digest = sha256_text(canonical_json(payload))
        return payload, digest

    def unit_snapshot(self, case_id: str, unit_id: str) -> tuple[dict[str, Any], str]:
        case = self.case_repo.get(case_id)
        try:
            from app.infrastructure.repositories import UnitMappingRepository
            from app.services.reporting_units import compile_reporting_units, unit_to_dict

            raw_facts = self.facts.list_for_case(case_id)
            raw_evidence = self.evidence.list_for_case(case_id)
            raw_conflicts = self.conflicts.list_for_case(case_id)
            mappings = UnitMappingRepository(self.session).list_for_case(case_id)
            decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
            units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
            target = next((u for u in units if u.unit_id == unit_id), None)
            if target is None:
                raise ValidationFailed("unit not found")
            from app.services.readiness import assess_unit

            unit_readiness = assess_unit(target, raw_facts, raw_evidence, raw_conflicts, units, case.route)
            payload = {
                "unit": unit_to_dict(target),
                "readiness": unit_readiness,
                "route": case.route.value,
                "notice_version": NOTICE_VERSION,
                "template_version": TEMPLATE_VERSION,
            }
            # include canonical snapshot helpers
            from app.domain.policies import canonical_json as _cj
            from app.domain.policies import sha256_text as _st

            digest = _st(_cj(payload))
            return payload, digest
        except ValidationFailed:
            raise
        except Exception as exc:
            raise ValidationFailed("unit snapshot error") from exc

    def approve(self, case_id: str, session_id: str, snapshot_hash: str, accepted_notice: bool, target_id: str | None = None) -> ApprovalRecord:
        if not accepted_notice:
            raise ValidationFailed("persetujuan harus eksplisit")
        case = self.cases.get_owned(case_id, session_id)
        if case.state != State.WAITING_APPROVAL:
            raise ApprovalRequired("kasus belum menunggu persetujuan")
        # For unit-scoped approval, check only relevant blocking conflicts
        if target_id:
            # validate unit exists and check unit-scoped blocking
            from app.infrastructure.repositories import UnitMappingRepository
            from app.services.reporting_units import compile_reporting_units

            raw_facts = self.facts.list_for_case(case_id)
            raw_evidence = self.evidence.list_for_case(case_id)
            raw_conflicts = self.conflicts.list_for_case(case_id)
            mappings = UnitMappingRepository(self.session).list_for_case(case_id)
            decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
            units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
            target_unit = next((u for u in units if u.unit_id == target_id), None)
            if target_unit is None:
                raise ValidationFailed("unit not found")
            if str(getattr(target_unit, "mapping_status", "")) == "AMBIGUOUS":
                raise ValidationFailed("unit masih ambiguous")
            # check unit relevant blocking conflicts
            relevant = []
            for c in raw_conflicts:
                if c.severity.value != "BLOCKING" or c.status.value != "OPEN":
                    continue
                # global vs unit-scoped
                fact_set = set(c.fact_ids)
                # if conflict touches target unit -> block
                if fact_set.issubset(set(target_unit.fact_ids)):
                    relevant.append(c)
                elif any(f not in set(target_unit.fact_ids) for f in fact_set):
                    # if conflict spans multiple units, check if it involves target unit at all
                    if fact_set & set(target_unit.fact_ids):
                        relevant.append(c)
                    else:
                        # conflict of other unit -> ignore for this unit approval
                        continue
                else:
                    # global conflict without specific unit? treat as blocking
                    relevant.append(c)
            if relevant:
                raise ValidationFailed("masih ada konflik yang memblokir unit ini")
            _, digest = self.unit_snapshot(case_id, target_id)
            if digest != snapshot_hash:
                raise ApprovalHashMismatch("snapshot unit tidak cocok")
            # revoke existing unit approval for same target
            existing = self.approvals.active_for_target(case_id, target_id)
            if existing is not None:
                existing.revoked_at = now_utc()
                existing.revoke_reason = "replaced"
                self.approvals.save(existing)
            scope = ApprovalScope.REPORTING_UNIT_HANDOFF
            profile_version = None
            try:
                from app.services.readiness import load_profile

                profile_version = load_profile()["profile_version"]
            except Exception:
                profile_version = None
            record = ApprovalRecord(
                approval_id=new_id("appr"),
                case_id=case_id,
                actor="USER",
                scope=scope,
                snapshot_hash=digest,
                approved_at=now_utc(),
                notice_version=NOTICE_VERSION,
                target_id=target_id,
                profile_version=profile_version,
            )
            self.approvals.add(record)
            # For unit approvals, we keep case in WAITING_APPROVAL until at least one unit compiled? But we need to move to GENERATING for artifact generation per unit?
            # We will set case approved hash to unit's hash as latest? Keep case-level hash as well
            case.approved_snapshot_hash = digest
            self.cases.set_state(case, State.GENERATING, event_type="APPROVAL_GRANTED_UNIT")
            return record
        # case-level approval (legacy) - keep existing behavior but also include units snapshot validation
        # For multi-unit case, case-level approval should still be allowed only if no AMBIGUOUS units?
        # We enforce no AMBIGUOUS units for case-level
        try:
            from app.infrastructure.repositories import UnitMappingRepository
            from app.services.reporting_units import compile_reporting_units

            raw_facts = self.facts.list_for_case(case_id)
            raw_evidence = self.evidence.list_for_case(case_id)
            mappings = UnitMappingRepository(self.session).list_for_case(case_id)
            decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
            units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
            if any(str(getattr(u.mapping_status, "value", u.mapping_status)) == "AMBIGUOUS" for u in units):
                raise ValidationFailed("pasangan transaksi belum dipilih")
        except ValidationFailed:
            raise
        except Exception:
            units = []
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
            target_id=None,
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
