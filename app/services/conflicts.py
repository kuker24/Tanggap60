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
    # Per-evidence amount conflict (same evidence has multiple different amounts for same dest -> ambiguous amount)
    by_evid_amt: dict[str, list[FactRecord]] = defaultdict(list)
    for f in amounts:
        by_evid_amt[f.source_evidence_id].append(f)
    for evid, lst in by_evid_amt.items():
        norms = {f.normalized_value for f in lst if f.normalized_value}
        if len(norms) > 1 and len(lst) >= 2:
            # If same evidence has multiple dests, the different amounts likely belong to different dests (mapping ambiguity, not value conflict)
            dests_for_evid = [f for f in active if f.type in {FactType.ACCOUNT, FactType.PJP} and f.source_evidence_id == evid and "VICTIM" not in (f.raw_value or "")]
            if len({d.normalized_value or d.raw_value for d in dests_for_evid}) > 1:
                continue
            conflicts.append(
                ConflictRecord(
                    conflict_id=new_id("conf"),
                    case_id=case_id,
                    type=ConflictType.VALUE_MISMATCH,
                    fact_ids=[f.fact_id for f in lst],
                    severity=ConflictSeverity.BLOCKING,
                    status=ConflictStatus.OPEN,
                )
            )
    # Global amount conflict when single destination but multiple amounts (same transaction, different claim)
    # Detect when distinct destinations ==1 but multiple amount norms -> likely same transfer contested
    accounts = by_type.get(FactType.ACCOUNT, [])
    dests = [f for f in accounts if "DEST" in (f.raw_value or "")]
    if not dests:
        dests = [f for f in accounts if f.type == FactType.ACCOUNT]
    # Also consider PJP?
    unique_dest = {f.normalized_value or f.raw_value for f in dests}
    if len(amounts) >= 2 and len({f.normalized_value for f in amounts if f.normalized_value}) > 1:
        # if amounts are from different evidences but dest count is 1, treat as conflict for single-unit case
        # also if any amount is from communication evidence (chat) vs transaction, keep conflict for legacy hero
        # heuristic: if number of distinct transaction evidences ==1 and multiple amounts -> conflict
        # We approximate transaction evidences as those that have at least one ACCOUNT or DATETIME
        transaction_evid_ids = {f.source_evidence_id for f in active if f.type in {FactType.ACCOUNT, FactType.DATETIME}}
        amount_evid_ids = {f.source_evidence_id for f in amounts}
        # if amounts span >1 evidence but only one transaction evidence has account, it's still single unit
        if len(unique_dest) == 1 and len(amount_evid_ids) > 1:
            # check if not already flagged per-evidence
            if not any(c.type == ConflictType.VALUE_MISMATCH for c in conflicts):
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

    # Destination disagreement: only when same evidence has multiple different dests (ambiguous)
    by_evid_dest: dict[str, list[FactRecord]] = defaultdict(list)
    for f in dests:
        by_evid_dest[f.source_evidence_id].append(f)
    for evid, lst in by_evid_dest.items():
        uniq = {f.normalized_value or f.raw_value for f in lst}
        if len(uniq) > 1:
            conflicts.append(
                ConflictRecord(
                    conflict_id=new_id("conf"),
                    case_id=case_id,
                    type=ConflictType.SOURCE_DISAGREEMENT,
                    fact_ids=[f.fact_id for f in lst],
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
