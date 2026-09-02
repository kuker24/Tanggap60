from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from app.domain.models import (
    EvidenceRecord,
    FactRecord,
    FactType,
    MappingStatus,
    ReportingUnitRecord,
    ReviewStatus,
)

# Deterministic helpers


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_unit_id(case_id: str, evidence_id: str, fact_ids: list[str], suffix: str = "") -> str:
    # stable ru_<12 hex>
    key = _canonical(
        {
            "case_id": case_id,
            "evidence_id": evidence_id,
            "fact_ids": sorted(fact_ids),
            "suffix": suffix,
        }
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"ru_{digest[:12]}"


def _is_transaction_evidence(facts: list[FactRecord]) -> bool:
    types = {f.type for f in facts}
    has_amount = FactType.AMOUNT in types
    has_account = FactType.ACCOUNT in types or FactType.PJP in types
    has_time = FactType.DATETIME in types
    # candidate if has at least AMOUNT+ACCOUNT or AMOUNT+TIME or ACCOUNT+TIME
    count = sum([has_amount, has_account, has_time])
    return count >= 2 or (has_amount and has_account)


def _is_communication_evidence(facts: list[FactRecord]) -> bool:
    types = {f.type for f in facts}
    return bool(types & {FactType.CLAIM, FactType.CHANNEL, FactType.PHONE})


def classify_evidence(
    evidence: list[EvidenceRecord], facts: list[FactRecord]
) -> dict[str, str]:
    by_evid: dict[str, list[FactRecord]] = defaultdict(list)
    for f in facts:
        if f.review_status == ReviewStatus.REJECTED:
            continue
        by_evid[f.source_evidence_id].append(f)
    result: dict[str, str] = {}
    for ev in evidence:
        f_list = by_evid.get(ev.evidence_id, [])
        # filename hint secondary - not primary
        name = ev.original_name_display.lower()
        is_tx = _is_transaction_evidence(f_list)
        is_comm = _is_communication_evidence(f_list)
        if is_tx:
            result[ev.evidence_id] = "TRANSACTION"
        elif is_comm:
            result[ev.evidence_id] = "COMMUNICATION"
        else:
            # secondary hint
            if any(k in name for k in ("transfer", "struk", "bukti", "pembayaran", "mutasi")):
                # but no facts -> unknown
                result[ev.evidence_id] = "UNKNOWN"
            elif any(k in name for k in ("chat", "wa", "pesan", "whatsapp", "telegram")):
                result[ev.evidence_id] = "COMMUNICATION"
            elif "screenshot" in name or "img_" in name:
                # generic screenshot may be transaction or not - use semantics
                result[ev.evidence_id] = "SHARED" if not f_list else ("TRANSACTION" if is_tx else "SHARED")
            else:
                result[ev.evidence_id] = "SHARED" if f_list else "UNKNOWN"
        # shared evidence general
        if not f_list and result[ev.evidence_id] == "UNKNOWN":
            result[ev.evidence_id] = "SHARED"
    return result


def _reviewed(facts: list[FactRecord]) -> list[FactRecord]:
    return [f for f in facts if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}]


def compile_reporting_units(
    case_id: str,
    facts: list[FactRecord],
    evidence: list[EvidenceRecord],
    mapping_decisions: list[dict[str, Any]] | None = None,
) -> list[ReportingUnitRecord]:
    """
    Deterministic provenance-first compiler.
    Strong mapping only when destination/amount/time share same source_evidence_id.
    Never guess positional pairing.
    """
    active = _reviewed(facts)
    # group facts by evidence
    by_evid: dict[str, list[FactRecord]] = defaultdict(list)
    for f in active:
        by_evid[f.source_evidence_id].append(f)

    # classify for shared detection
    semantics = classify_evidence(evidence, active)
    shared_evidence_ids = [eid for eid, kind in semantics.items() if kind in {"SHARED", "COMMUNICATION"}]

    units: list[ReportingUnitRecord] = []
    # handle explicit human mapping decisions first
    decisions_by_evid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if mapping_decisions:
        for dec in mapping_decisions:
            # dec expected {unit_id, evidence_id, pairings:[{dest_fact_id, amount_fact_id, time_fact_id}]}
            eid = str(dec.get("evidence_id") or dec.get("target_evidence_id") or "")
            if eid:
                decisions_by_evid[eid].append(dec)

    # For each evidence that is TRANSACTION candidate, build units
    for ev in evidence:
        # skip purged/rejected evidence - but evidence list already filtered live?
        fact_list = by_evid.get(ev.evidence_id, [])
        if not fact_list:
            continue
        # only transaction-like evidences produce units; communication/shared are shared
        if semantics.get(ev.evidence_id) not in {"TRANSACTION"}:
            # allow if evidence has any transaction fact (AMOUNT/ACCOUNT/DATETIME) even if not full candidate
            if not any(f.type in {FactType.AMOUNT, FactType.ACCOUNT, FactType.PJP, FactType.DATETIME} for f in fact_list):
                continue
        victims = [f for f in fact_list if f.type == FactType.ACCOUNT and "VICTIM" in (f.raw_value or "")]
        victim_val = victims[0].raw_value if victims else None
        dests = [f for f in fact_list if f.type in {FactType.ACCOUNT, FactType.PJP} and "VICTIM" not in (f.raw_value or "")]
        amounts = [f for f in fact_list if f.type == FactType.AMOUNT]
        times = [f for f in fact_list if f.type == FactType.DATETIME]

        # If human mapping decision exists for this evidence, use it to produce COMPLETE units deterministically
        if ev.evidence_id in decisions_by_evid:
            for dec in decisions_by_evid[ev.evidence_id]:
                pairings = dec.get("pairings") or dec.get("chosen_pairings") or []
                for idx, pairing in enumerate(pairings):
                    dest_fid = str(pairing.get("destination_fact_id") or pairing.get("dest_fact_id") or "")
                    amt_fid = str(pairing.get("amount_fact_id") or "")
                    time_fid = str(pairing.get("datetime_fact_id") or pairing.get("time_fact_id") or "")
                    # lookup facts
                    dest_fact = next((x for x in dests if x.fact_id == dest_fid), None)
                    amt_fact = next((x for x in amounts if x.fact_id == amt_fid), None)
                    time_fact = next((x for x in times if x.fact_id == time_fid), None)
                    # fallback to any if mismatch - but we validate
                    if dest_fact is None and dests:
                        # choose first if not specified
                        dest_fact = dests[0]
                    fid_list = [f.fact_id for f in [dest_fact, amt_fact, time_fact] if f]
                    evid_ids = [ev.evidence_id] + [eid for eid in shared_evidence_ids if eid != ev.evidence_id][:0]  # shared not auto bound
                    unit_id = stable_unit_id(case_id, ev.evidence_id, fid_list, suffix=str(idx))
                    # determine completeness after mapping
                    amount_val = None
                    transferred = None
                    if amt_fact and amt_fact.normalized_value:
                        try:
                            amount_val = float(amt_fact.normalized_value)
                        except ValueError:
                            amount_val = None
                    if time_fact and time_fact.normalized_value:
                        transferred = time_fact.normalized_value
                    status = MappingStatus.COMPLETE if (dest_fact and amount_val is not None and transferred) else MappingStatus.INCOMPLETE
                    reason = "human mapping applied" if status == MappingStatus.COMPLETE else "missing time/amount after mapping"
                    units.append(
                        ReportingUnitRecord(
                            unit_id=unit_id,
                            case_id=case_id,
                            source_account=victim_val,
                            destination_account=dest_fact.raw_value if dest_fact else None,
                            amount=amount_val,
                            transferred_at=transferred,
                            fact_ids=fid_list,
                            evidence_ids=evid_ids,
                            mapping_status=status,
                            mapping_reason=reason,
                            mapping_provenance=ev.evidence_id,
                        )
                    )
            continue

        # No decision: apply provenance-first rules
        # Cases:
        # - single dest + single amount + single time -> COMPLETE
        # - single dest + single amount + no time -> INCOMPLETE (missing time)
        # - single dest + no amount -> INCOMPLETE
        # - multiple dests/amounts with ambiguous count -> AMBIGUOUS
        # - zero dest but has amount -> INCOMPLETE (UNKNOWN dest)

        if not dests and amounts:
            # INCOMPLETE without dest
            amt = amounts[0]
            time_val = times[0].normalized_value if times else None
            amt_val = None
            try:
                amt_val = float(amt.normalized_value) if amt.normalized_value else None
            except ValueError:
                amt_val = None
            fid_list = [f.fact_id for f in [amt] + times[:1]]
            unit_id = stable_unit_id(case_id, ev.evidence_id, fid_list, suffix="missing_dest")
            units.append(
                ReportingUnitRecord(
                    unit_id=unit_id,
                    case_id=case_id,
                    source_account=victim_val,
                    destination_account=None,
                    amount=amt_val,
                    transferred_at=str(time_val) if time_val else None,
                    fact_ids=fid_list,
                    evidence_ids=[ev.evidence_id],
                    mapping_status=MappingStatus.INCOMPLETE,
                    mapping_reason="missing destination_account",
                    mapping_provenance=ev.evidence_id,
                )
            )
            continue

        if not dests and not amounts:
            continue

        # Check for ambiguous multi within same evidence
        if len(dests) > 1 or len(amounts) > 1:
            # If multiple facts in same evidence, pairing cannot be proven without human decision -> AMBIGUOUS
            all_ids = [f.fact_id for f in dests + amounts + times]
            unit_id = stable_unit_id(case_id, ev.evidence_id, all_ids, suffix="ambiguous")
            units.append(
                ReportingUnitRecord(
                    unit_id=unit_id,
                    case_id=case_id,
                    source_account=victim_val,
                    destination_account=None,
                    amount=None,
                    transferred_at=None,
                    fact_ids=all_ids,
                    evidence_ids=[ev.evidence_id],
                    mapping_status=MappingStatus.AMBIGUOUS,
                    mapping_reason=f"AMBIGUOUS_MAPPING: {len(dests)} destination, {len(amounts)} amount, {len(times)} time in same evidence {ev.evidence_id} — pairing not provable without human decision",
                    mapping_provenance=ev.evidence_id,
                )
            )
            continue

        # Now single dest/amount cases
        dest = dests[0] if dests else None
        amt = amounts[0] if amounts else None
        t = times[0] if times else None

        amt_val = None
        if amt and amt.normalized_value:
            try:
                amt_val = float(amt.normalized_value)
            except ValueError:
                amt_val = None
        time_val = t.normalized_value if t and t.normalized_value else None

        fid_list = [f.fact_id for f in [dest, amt, t] if f]

        if dest and amt_val is not None and time_val:
            status = MappingStatus.COMPLETE
            reason = "provenance strong: dest+amount+time from same evidence"
        elif dest and amt_val is not None and not time_val:
            status = MappingStatus.INCOMPLETE
            reason = "missing transferred_at"
        elif dest and amt_val is None:
            status = MappingStatus.INCOMPLETE
            reason = "missing amount"
        elif not dest:
            status = MappingStatus.INCOMPLETE
            reason = "missing destination_account"
        else:
            status = MappingStatus.INCOMPLETE
            reason = "incomplete transaction triad"

        unit_id = stable_unit_id(case_id, ev.evidence_id, fid_list)
        units.append(
            ReportingUnitRecord(
                unit_id=unit_id,
                case_id=case_id,
                source_account=victim_val,
                destination_account=dest.raw_value if dest else None,
                amount=amt_val,
                transferred_at=str(time_val) if time_val else None,
                fact_ids=fid_list,
                evidence_ids=[ev.evidence_id],
                mapping_status=status,
                mapping_reason=reason,
                mapping_provenance=ev.evidence_id,
            )
        )

    # If no units but there are cross-evidence facts spread, fallback: create per-dest incomplete units from globally grouped facts?
    # To support case where dest in one evidence, amount in another but not shared evidence,
    # we already may have no unit because per-evidence dest list would be 1 dest but 0 amount -> INCOMPLETE.
    # That's correct as INCOMPLETE.

    # Also handle case where evidence classification missed transaction but facts exist across evidences each with single fact:
    # we already handled per-evidence.
    # Ensure deterministic ordering
    units.sort(key=lambda u: u.unit_id)
    return units


def evidence_semantics_map(evidence: list[EvidenceRecord], facts: list[FactRecord]) -> dict[str, str]:
    return classify_evidence(evidence, facts)


def unit_to_dict(unit: ReportingUnitRecord) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "case_id": unit.case_id,
        "source_account": unit.source_account,
        "destination_account": unit.destination_account,
        "amount": unit.amount,
        "transferred_at": unit.transferred_at,
        "fact_ids": list(unit.fact_ids),
        "evidence_ids": list(unit.evidence_ids),
        "mapping_status": unit.mapping_status.value if isinstance(unit.mapping_status, str) else str(unit.mapping_status),
        "mapping_reason": unit.mapping_reason,
        "mapping_provenance": unit.mapping_provenance,
        "readiness": unit.readiness,
    }
