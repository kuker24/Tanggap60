from app.domain.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    MappingStatus,
    ReportingUnitRecord,
)
from app.services.next_action import recommend_next_action


def _unit(uid, status, dest="DEST", amt=1000, t="2026-09-23T09:13:00Z"):
    return ReportingUnitRecord(
        unit_id=uid,
        case_id="case-x",
        source_account=None,
        destination_account=dest,
        amount=amt,
        transferred_at=t,
        fact_ids=[uid+"-f1", uid+"-f2"],
        evidence_ids=[uid+"-ev"],
        mapping_status=status,
        mapping_reason="test",
        mapping_provenance="ev",
    )


def _conflict(cid, fact_ids, severity=ConflictSeverity.BLOCKING, status=ConflictStatus.OPEN):
    return ConflictRecord(conflict_id=cid, case_id="case-x", type=ConflictType.VALUE_MISMATCH, fact_ids=fact_ids, severity=severity, status=status)


def test_ready_plus_incomplete_act_ready():
    a = _unit("ru_a", MappingStatus.COMPLETE, dest="A")
    b = ReportingUnitRecord(unit_id="ru_b", case_id="case-x", source_account=None, destination_account="B", amount=500, transferred_at=None, fact_ids=["ru_b-f1"], evidence_ids=["ev2"], mapping_status=MappingStatus.INCOMPLETE, mapping_reason="missing", mapping_provenance="ev2")
    readiness = {"ru_a": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_b": {"BANK_PJP": "NEEDS_ACTION", "IASC": "NEEDS_ACTION"}}
    act = recommend_next_action(case_id="case-x", units=[a,b], conflicts=[], readiness_by_unit=readiness)
    assert act.target_unit_id == "ru_a"
    assert act.code in ("CONTACT_BANK_PJP", "PREPARE_IASC_UNIT")


def test_ready_plus_ambiguous_act_ready():
    a = _unit("ru_a", MappingStatus.COMPLETE, dest="A")
    b = _unit("ru_b", MappingStatus.AMBIGUOUS, dest=None, amt=None, t=None)
    b.fact_ids = ["ru_b-f1", "ru_b-f2", "ru_b-f3"]
    readiness = {"ru_a": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_b": {"BANK_PJP": "BLOCKED", "IASC": "BLOCKED"}}
    act = recommend_next_action(case_id="case-x", units=[a,b], conflicts=[], readiness_by_unit=readiness)
    assert act.target_unit_id == "ru_a"


def test_ready_plus_scoped_conflict_act_ready():
    a = _unit("ru_a", MappingStatus.COMPLETE, dest="A")
    b = _unit("ru_b", MappingStatus.COMPLETE, dest="B")
    # conflict scoped to b only
    c = _conflict("c1", ["ru_b-f1", "ru_b-f2"])
    readiness = {"ru_a": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_b": {"BANK_PJP": "BLOCKED", "IASC": "BLOCKED"}}
    act = recommend_next_action(case_id="case-x", units=[a,b], conflicts=[c], readiness_by_unit=readiness)
    assert act.target_unit_id == "ru_a"
    assert act.code in ("CONTACT_BANK_PJP", "PREPARE_IASC_UNIT")


def test_ready_plus_global_conflict_resolve_global():
    a = _unit("ru_a", MappingStatus.COMPLETE, dest="A")
    b = _unit("ru_b", MappingStatus.COMPLETE, dest="B")
    # global conflict spans both or none matching single unit
    c = _conflict("c1", ["ru_a-f1", "ru_b-f1"])
    readiness = {"ru_a": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_b": {"BANK_PJP": "READY", "IASC": "READY"}}
    act = recommend_next_action(case_id="case-x", units=[a,b], conflicts=[c], readiness_by_unit=readiness)
    assert act.code == "RESOLVE_CONFLICT"


def test_no_ready_ambiguous_resolve_mapping():
    b = _unit("ru_b", MappingStatus.AMBIGUOUS, dest=None, amt=None, t=None)
    b.fact_ids = ["ru_b-f1", "ru_b-f2"]
    act = recommend_next_action(case_id="case-x", units=[b], conflicts=[], readiness_by_unit={})
    assert act.code == "RESOLVE_UNIT_MAPPING"
    assert act.target_unit_id == "ru_b"


def test_no_ready_scoped_conflict_resolve_affected():
    b = _unit("ru_b", MappingStatus.COMPLETE, dest="B")
    c = _conflict("c1", ["ru_b-f1"])
    readiness = {"ru_b": {"BANK_PJP": "BLOCKED", "IASC": "BLOCKED"}}
    act = recommend_next_action(case_id="case-x", units=[b], conflicts=[c], readiness_by_unit=readiness)
    assert act.code == "RESOLVE_CONFLICT"
    assert act.target_unit_id == "ru_b"


def test_two_ready_deterministic_ordering():
    a = _unit("ru_a", MappingStatus.COMPLETE, dest="A")
    b = _unit("ru_b", MappingStatus.COMPLETE, dest="B")
    readiness = {"ru_a": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_b": {"BANK_PJP": "READY", "IASC": "READY"}}
    act = recommend_next_action(case_id="case-x", units=[b,a], conflicts=[], readiness_by_unit=readiness)
    # sorted by unit_id, so ru_a should be chosen even if input order reversed
    assert act.target_unit_id == "ru_a"
