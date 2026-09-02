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
    active = [f for f in facts if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}]
    destinations = [f for f in active if f.type == FactType.ACCOUNT and "DEST" in (f.raw_value or "")]
    if not destinations:
        destinations = [f for f in active if f.type == FactType.ACCOUNT]
    amounts = [f for f in active if f.type == FactType.AMOUNT]
    times = [f for f in active if f.type == FactType.DATETIME]
    victims = [f for f in active if f.type == FactType.ACCOUNT and "VICTIM" in (f.raw_value or "")]
    amount_value = float(amounts[0].normalized_value) if amounts and amounts[0].normalized_value else 0.0
    transferred = times[0].normalized_value if times and times[0].normalized_value else ""
    victim = victims[0].raw_value if victims else None
    groups: list[TransactionGroupRecord] = []
    if not destinations:
        if amount_value:
            groups.append(
                TransactionGroupRecord(
                    transaction_group_id=new_id("tx"),
                    case_id=case_id,
                    victim_account=victim,
                    destination_account="UNKNOWN",
                    amount=amount_value,
                    transferred_at=str(transferred),
                    evidence_ids=evidence_ids,
                    readiness="MISSING_DESTINATION",
                )
            )
        return groups
    unique: dict[str, object] = {}
    for dest in destinations:
        key = dest.normalized_value or dest.raw_value
        unique[key] = dest
    for key, dest in unique.items():
        groups.append(
            TransactionGroupRecord(
                transaction_group_id=new_id("tx"),
                case_id=case_id,
                victim_account=victim,
                destination_account=dest.raw_value,
                amount=amount_value,
                transferred_at=str(transferred),
                evidence_ids=evidence_ids,
                readiness="READY" if amount_value and transferred else "INCOMPLETE",
            )
        )
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
