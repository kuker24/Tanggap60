from __future__ import annotations

from app.domain.models import FactRecord, FactType, ReviewStatus, TransactionGroupRecord
from app.domain.policies import route_from_condition
from app.domain.states import DeclaredCondition, Route
from app.services.ids import new_id


def infer_loss(facts: list[FactRecord]) -> bool:
    amounts = [
        f
        for f in facts
        if f.type == FactType.AMOUNT and f.review_status != ReviewStatus.REJECTED
    ]
    return bool(amounts)


def apply_route(condition: DeclaredCondition, facts: list[FactRecord]) -> tuple[Route, str, float, bool]:
    return route_from_condition(condition, has_loss_facts=infer_loss(facts))


def group_transactions(case_id: str, facts: list[FactRecord], evidence_ids: list[str]) -> list[TransactionGroupRecord]:
    """
    Provenance-first grouping. No positional guessing.
    Only groups facts that share the same source_evidence_id.
    If pairing not provable -> AMBIGUOUS.
    """
    from collections import defaultdict

    active = [f for f in facts if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}]
    if not active:
        return []
    # group facts by evidence
    by_evid: dict[str, list[FactRecord]] = defaultdict(list)
    for f in active:
        by_evid[f.source_evidence_id].append(f)
    victims = [f for f in active if f.type == FactType.ACCOUNT and "VICTIM" in (f.raw_value or "")]
    victim = victims[0].raw_value if victims else None

    groups: list[TransactionGroupRecord] = []
    # Evidence semantics: only evidence that looks transactional produces groups
    for eid, fact_list in by_evid.items():
        types = {f.type for f in fact_list}
        has_amount = FactType.AMOUNT in types
        has_account = FactType.ACCOUNT in types or FactType.PJP in types
        has_time = FactType.DATETIME in types
        is_transaction_candidate = (has_amount and has_account) or (has_amount and has_time) or (has_account and has_time)
        # also consider evidence kind secondary via name? evidence_ids includes all, but we treat any fact-bearing evidence as candidate
        # If not transactional (e.g., only CLAIM/CHANNEL), skip as shared evidence
        if not is_transaction_candidate:
            # Check if it has only communication facts -> shared, don't make transaction groups
            if FactType.CLAIM in types or FactType.CHANNEL in types or FactType.PHONE in types:
                continue
            # If has single amount but no account/time, still maybe incomplete transaction? Create incomplete if amount present
            if not has_amount:
                continue
        dests = [f for f in fact_list if f.type in {FactType.ACCOUNT, FactType.PJP} and "VICTIM" not in (f.raw_value or "")]
        # fallback: if no DEST marker, treat all accounts as dest for this evidence
        if not dests:
            dests = [f for f in fact_list if f.type == FactType.ACCOUNT]
        amounts = [f for f in fact_list if f.type == FactType.AMOUNT]
        times = [f for f in fact_list if f.type == FactType.DATETIME]

        # Ambiguous multi within same evidence -> do not guess
        if len(dests) > 1 or len(amounts) > 1:
            # Create single ambiguous group representing unresolved mapping
            groups.append(
                TransactionGroupRecord(
                    transaction_group_id=new_id("tx"),
                    case_id=case_id,
                    victim_account=victim,
                    destination_account="AMBIGUOUS",
                    amount=0.0,
                    transferred_at="",
                    evidence_ids=[eid],
                    readiness="AMBIGUOUS_MAPPING",
                )
            )
            continue

        # No dest but has amount -> incomplete
        if not dests and amounts:
            amt_val = 0.0
            if amounts[0].normalized_value:
                try:
                    amt_val = float(amounts[0].normalized_value)
                except ValueError:
                    amt_val = 0.0
            tr = times[0].normalized_value if times and times[0].normalized_value else ""
            groups.append(
                TransactionGroupRecord(
                    transaction_group_id=new_id("tx"),
                    case_id=case_id,
                    victim_account=victim,
                    destination_account="UNKNOWN",
                    amount=amt_val,
                    transferred_at=str(tr),
                    evidence_ids=[eid],
                    readiness="MISSING_DESTINATION",
                )
            )
            continue

        if not dests:
            continue

        # Single dest case
        dest = dests[0]
        amt_val = 0.0
        has_amt = False
        if amounts:
            try:
                amt_val = float(amounts[0].normalized_value) if amounts[0].normalized_value else 0.0
                has_amt = amt_val != 0.0
            except ValueError:
                has_amt = False
        has_time = bool(times and times[0].normalized_value)
        transferred = times[0].normalized_value if times and times[0].normalized_value else ""

        if has_amt and has_time:
            readiness = "READY"
        elif has_amt and not has_time:
            readiness = "INCOMPLETE"
        elif not has_amt and has_time:
            readiness = "INCOMPLETE"
        else:
            readiness = "INCOMPLETE"

        groups.append(
            TransactionGroupRecord(
                transaction_group_id=new_id("tx"),
                case_id=case_id,
                victim_account=victim,
                destination_account=dest.raw_value,
                amount=amt_val,
                transferred_at=str(transferred),
                evidence_ids=[eid],
                readiness=readiness,
            )
        )

    # If still no groups but there were amounts/dests cross-evidence spread? Fallback: legacy unique dests but without guessing?
    # We have already produced per-evidence groups, which is provenance-safe.
    # Ensure deterministic order
    groups.sort(key=lambda g: g.transaction_group_id)
    return groups


def readiness_notes(groups: list[TransactionGroupRecord], has_chat: bool, has_transfer: bool) -> list[str]:
    notes: list[str] = []
    if not has_transfer:
        notes.append("Bukti transfer belum ditemukan. Unggah atau tandai belum tersedia.")
    if not has_chat:
        notes.append("Bukti komunikasi belum ditemukan.")
    if any(g.destination_account == "UNKNOWN" for g in groups):
        notes.append("Rekening tujuan belum lengkap.")
    notes.append("Identitas/KTP diisi langsung di portal resmi. ENTER_DIRECTLY_ON_OFFICIAL_PORTAL")
    return notes
