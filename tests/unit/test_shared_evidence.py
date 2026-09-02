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
from app.domain.states import Route
from app.services.readiness import assess_units
from app.services.reporting_units import compile_reporting_units

HASH = "a"*64
NOW = datetime.now(UTC)

def _ev(eid, name):
    return EvidenceRecord(evidence_id=eid, case_id="case-x", kind=EvidenceKind.IMAGE, original_name_display=name, storage_key=eid, mime="image/png", size_bytes=10, sha256=HASH, page_count=1, status=EvidenceStatus.EXTRACTED, retention_until=NOW)

def _fact(fid, ftype, raw, norm, evid, status=ReviewStatus.CONFIRMED):
    return FactRecord(fact_id=fid, case_id="case-x", type=ftype, raw_value=raw, normalized_value=norm, criticality=Criticality.CRITICAL, confidence=0.9, review_status=status, source_evidence_id=evid, source_page=1, source_bbox="box", source_excerpt_hash=HASH)

def test_shared_chat_not_duplicated_as_unit_provenance():
    ev1 = _ev("ev1", "transfer_a.png")
    ev2 = _ev("ev2", "transfer_b.png")
    ev_chat = _ev("ev_chat", "chat.png")
    facts = [
        _fact("f-amt1", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc1", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-time1", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
        _fact("f-amt2", FactType.AMOUNT, "Rp750.000", "750000", "ev2"),
        _fact("f-acc2", FactType.ACCOUNT, "DEMO-DEST-B", "DEMO-DEST-B", "ev2"),
        _fact("f-time2", FactType.DATETIME, "2026-09-23T09:47:00Z", "2026-09-23T09:47:00Z", "ev2"),
        _fact("f-claim", FactType.CLAIM, "kirim dulu", "kirim dulu", "ev_chat"),
    ]
    units = compile_reporting_units("case-x", facts, [ev1, ev2, ev_chat])
    assert len(units) == 2
    for u in units:
        assert "ev_chat" not in u.evidence_ids, "shared chat should not be auto-bound as unit provenance"
        assert "ev_chat" not in u.fact_ids

def test_iasc_readiness_labels_shared():
    ev1 = _ev("ev1", "transfer_a.png")
    ev_chat = _ev("ev_chat", "chat.png")
    facts = [
        _fact("f-amt1", FactType.AMOUNT, "Rp2.000.000", "2000000", "ev1"),
        _fact("f-acc1", FactType.ACCOUNT, "DEMO-DEST-A", "DEMO-DEST-A", "ev1"),
        _fact("f-time1", FactType.DATETIME, "2026-09-23T09:13:00Z", "2026-09-23T09:13:00Z", "ev1"),
        _fact("f-claim", FactType.CLAIM, "kirim dulu", "kirim dulu", "ev_chat"),
    ]
    units = compile_reporting_units("case-x", facts, [ev1, ev_chat])
    # need at least one unit
    assert len(units) == 1
    report = assess_units(case_id="case-x", units=units, facts=facts, evidence=[ev1, ev_chat], conflicts=[], route=Route.POST_INCIDENT_RESPONSE)
    # unit's IASC communication should be MET but labeled INCIDENT_SHARED
    unit_rep = report["units"][0]
    iasc = next(ch for ch in unit_rep["channels"] if ch["channel"] == "IASC")
    comm_check = next(ck for ck in iasc["checks"] if ck["check_id"] == "IASC_COMMUNICATION_EVIDENCE")
    assert comm_check["status"] == "MET"
    assert "INCIDENT_SHARED" in comm_check["reason"] or "insiden" in comm_check["reason"].lower()
