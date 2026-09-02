from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import (
    Criticality,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    FactRecord,
    FactType,
    ReviewStatus,
)
from app.services.reporting_units import compile_reporting_units

HASH = "a" * 64
NOW = datetime.now(UTC)


def _ev(eid: str, name: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=eid,
        case_id="case-x",
        kind=EvidenceKind.IMAGE,
        original_name_display=name,
        storage_key=eid,
        mime="image/png",
        size_bytes=10,
        sha256=HASH,
        page_count=1,
        status=EvidenceStatus.EXTRACTED,
        retention_until=NOW,
    )


def _fact(fid: str, ftype: FactType, raw: str, norm: str | None, evid: str, status=ReviewStatus.CONFIRMED) -> FactRecord:
    return FactRecord(
        fact_id=fid,
        case_id="case-x",
        type=ftype,
        raw_value=raw,
        normalized_value=norm,
        criticality=Criticality.CRITICAL,
        confidence=0.9,
        review_status=status,
        source_evidence_id=evid,
        source_page=1,
        source_bbox="p1:box",
        source_excerpt_hash=HASH,
    )


def test_single_transaction_one_unit():
    ev = _ev("ev1", "transfer.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-time", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert len(units) == 1
    assert units[0].mapping_status == "COMPLETE"
    assert units[0].destination_account == "DEMO-DEST-A"
    assert units[0].amount == 2000000


def test_two_independent_transaction_evidence_two_units():
    ev1 = _ev("ev1", "transfer_a.png")
    ev2 = _ev("ev2", "transfer_b.png")
    facts = [
        _fact("f-amt1", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc1", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-time1", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
        _fact("f-amt2", FactType.AMOUNT, "Rp750.000", "750000", "ev2"),
        _fact("f-acc2", FactType.ACCOUNT, "DEMO-DEST-B", "DEMO-DEST-B", "ev2"),
        _fact("f-time2", FactType.DATETIME, "2026-09-23T09:47:00Z", "2026-09-23T09:47:00Z", "ev2"),
    ]
    units = compile_reporting_units("case-x", facts, [ev1, ev2])
    assert len(units) == 2
    dests = {u.destination_account for u in units}
    assert dests == {"DEMO-DEST-A", "DEMO-DEST-B"}


def test_same_evidence_multiple_amounts_ambiguous():
    ev = _ev("ev1", "ambiguous.png")
    facts = [
        _fact("f-amt1", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-amt2", FactType.AMOUNT, "Rp750.000", "750000", "ev1"),
        _fact("f-acc1", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-acc2", FactType.ACCOUNT, "DEMO-DEST-B", "DEMO-DEST-B", "ev1"),
        _fact("f-time1", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
        _fact("f-time2", FactType.DATETIME, "2026-09-23T09:47:00Z", "2026-09-23T09:47:00Z", "ev1"),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert len(units) == 1
    assert units[0].mapping_status == "AMBIGUOUS"


def test_no_positional_pairing():
    # 2 dests in ev1, 2 amounts in ev2 but different evidences - should not guess cross pairing
    ev1 = _ev("ev1", "transfer_a.png")
    ev2 = _ev("ev2", "transfer_b.png")
    facts = [
        _fact("f-acc1", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-acc2", FactType.ACCOUNT, "DEMO-DEST-B", "DEMO-DEST-B", "ev1"),
        _fact("f-amt1", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev2"),
        _fact("f-amt2", FactType.AMOUNT, "Rp750.000", "750000", "ev2"),
    ]
    units = compile_reporting_units("case-x", facts, [ev1, ev2])
    # ev1 has 2 dests -> ambiguous, ev2 has 2 amounts but no dest -> incomplete unknown dest
    # Should not produce positional pairing like A->2M, B->750k via order
    # At least should have ambiguous or incomplete, not 2 complete positional pairs
    assert any(u.mapping_status == "AMBIGUOUS" for u in units)


def test_stable_unit_id():
    ev = _ev("ev1", "transfer.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
    ]
    u1 = compile_reporting_units("case-x", facts, [ev])
    u2 = compile_reporting_units("case-x", facts, [ev])
    assert u1[0].unit_id == u2[0].unit_id
    assert u1[0].unit_id.startswith("ru_")


def test_rejected_fact_ignored():
    ev = _ev("ev1", "transfer.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-bad", FactType.AMOUNT, "Rp999.000", "999000", "ev1", status=ReviewStatus.REJECTED),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert len(units) == 1
    assert units[0].amount == 2000000


def test_missing_time_incomplete():
    ev = _ev("ev1", "transfer.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp750.000", "750000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-B", "DEMO-DEST-B", "ev1"),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert units[0].mapping_status == "INCOMPLETE"
    assert "transferred_at" in units[0].mapping_reason or "missing" in units[0].mapping_reason.lower()


def test_missing_destination_incomplete():
    ev = _ev("ev1", "transfer.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-time", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert units[0].mapping_status == "INCOMPLETE"


def test_shared_chat_remains_shared():
    ev1 = _ev("ev1", "transfer.png")
    ev2 = _ev("ev2", "chat.png")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-claim", FactType.CLAIM, "kirim dulu", "kirim dulu", "ev2"),
    ]
    units = compile_reporting_units("case-x", facts, [ev1, ev2])
    # chat should not produce a unit, only transfer
    assert len(units) == 1
    assert units[0].destination_account == "DEMO-DEST-A"


def test_filename_img_1234_still_recognized_semantically():
    ev = _ev("ev1", "IMG_7821.jpg")
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-time", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
    ]
    units = compile_reporting_units("case-x", facts, [ev])
    assert len(units) == 1
    assert units[0].mapping_status == "COMPLETE"
