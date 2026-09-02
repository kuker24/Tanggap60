from __future__ import annotations

from collections import defaultdict

from app.domain.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    FactRecord,
    FactType,
    ReviewStatus,
)
from app.services.ids import new_id


def detect_conflicts(case_id: str, facts: list[FactRecord]) -> list[ConflictRecord]:
    active = [f for f in facts if f.review_status != ReviewStatus.REJECTED]
    conflicts: list[ConflictRecord] = []
    by_type: dict[FactType, list[FactRecord]] = defaultdict(list)
    for fact in active:
        by_type[fact.type].append(fact)
    amounts = by_type.get(FactType.AMOUNT, [])
    norms = {f.normalized_value for f in amounts if f.normalized_value}
    if len(norms) > 1 and len(amounts) >= 2:
        conflicts.append(
            ConflictRecord(
                conflict_id=new_id("conf"),
                case_id=case_id,
                type=ConflictType.VALUE_MISMATCH,
                fact_ids=[f.fact_id for f in amounts],
                severity=ConflictSeverity.BLOCKING,
                status=ConflictStatus.OPEN,
            )
        )
    accounts = by_type.get(FactType.ACCOUNT, [])
    dests = [f for f in accounts if "DEST" in (f.raw_value or "")]
    unique_dest = {f.normalized_value or f.raw_value for f in dests}
    if len(unique_dest) > 1:
        conflicts.append(
            ConflictRecord(
                conflict_id=new_id("conf"),
                case_id=case_id,
                type=ConflictType.SOURCE_DISAGREEMENT,
                fact_ids=[f.fact_id for f in dests],
                severity=ConflictSeverity.WARNING,
                status=ConflictStatus.OPEN,
            )
        )
    return conflicts


def missing_fields(facts: list[FactRecord]) -> list[str]:
    types = {f.type for f in facts if f.review_status != ReviewStatus.REJECTED}
    missing: list[str] = []
    if FactType.AMOUNT not in types:
        missing.append("AMOUNT")
    if FactType.ACCOUNT not in types:
        missing.append("ACCOUNT")
    if FactType.DATETIME not in types:
        missing.append("DATETIME")
    return missing
