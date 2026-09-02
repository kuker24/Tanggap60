from app.domain.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    MappingStatus,
    ReportingUnitRecord,
)
from app.services.next_action import recommend_next_action


def _unit(uid, status, dest="DEMO-DEST-01", amt=1000000, t="2026-09-23T09:13:00Z"):
    return ReportingUnitRecord(
        unit_id=uid,
        case_id="case-x",
        source_account=None,
        destination_account=dest,
        amount=amt,
        transferred_at=t,
        fact_ids=[uid+"-f1"],
        evidence_ids=[uid+"-ev"],
        mapping_status=status,
        mapping_reason="test",
        mapping_provenance="ev1",
    )

def test_blocking_conflict_priority():
    units = [_unit("ru1", MappingStatus.COMPLETE)]
    conflicts = [ConflictRecord(conflict_id="c1", case_id="case-x", type=ConflictType.VALUE_MISMATCH, fact_ids=["ru1-f1"], severity=ConflictSeverity.BLOCKING, status=ConflictStatus.OPEN)]
    act = recommend_next_action(case_id="case-x", units=units, conflicts=conflicts)
    assert act.code == "RESOLVE_CONFLICT"

def test_ambiguous_mapping_priority():
    units = [_unit("ru1", MappingStatus.AMBIGUOUS), _unit("ru2", MappingStatus.COMPLETE)]
    act = recommend_next_action(case_id="case-x", units=units, conflicts=[])
    assert act.code == "RESOLVE_UNIT_MAPPING"
    assert act.target_unit_id == "ru1"

def test_ready_unit_before_incomplete():
    # One complete ready, one incomplete missing time -> should prioritize ready (CONTACT_BANK_PJP) per hero expectation
    complete = _unit("ru_complete", MappingStatus.COMPLETE, dest="DEMO-DEST-A")
    incomplete = ReportingUnitRecord(unit_id="ru_inc", case_id="case-x", source_account=None, destination_account="DEMO-DEST-B", amount=750000, transferred_at=None, fact_ids=["ru_inc-f1"], evidence_ids=["ev2"], mapping_status=MappingStatus.INCOMPLETE, mapping_reason="missing", mapping_provenance="ev2")
    readiness = {"ru_complete": {"BANK_PJP": "READY", "IASC": "READY"}, "ru_inc": {"BANK_PJP": "NEEDS_ACTION", "IASC": "NEEDS_ACTION"}}
    act = recommend_next_action(case_id="case-x", units=[complete, incomplete], conflicts=[], readiness_by_unit=readiness)
    assert act.code in {"CONTACT_BANK_PJP", "PREPARE_IASC_UNIT"}
    assert act.target_unit_id == "ru_complete"

def test_missing_time_then_ready():
    incomplete = ReportingUnitRecord(unit_id="ru_inc", case_id="case-x", source_account=None, destination_account="DEMO-DEST-B", amount=750000, transferred_at=None, fact_ids=["f1"], evidence_ids=["ev1"], mapping_status=MappingStatus.INCOMPLETE, mapping_reason="missing", mapping_provenance="ev1")
    act = recommend_next_action(case_id="case-x", units=[incomplete], conflicts=[], readiness_by_unit={})
    assert act.code == "CONFIRM_TRANSACTION_TIME"

def test_police_after_financial():
    complete = _unit("ru1", MappingStatus.COMPLETE)
    act = recommend_next_action(case_id="case-x", units=[complete], conflicts=[], readiness_by_unit={"ru1": {"BANK_PJP": "READY", "IASC": "READY"}}, incident_police_ready=False)
    # Since ready units exist, should return contact bank first, not police
    assert act.code in {"CONTACT_BANK_PJP", "PREPARE_IASC_UNIT"}
    act2 = recommend_next_action(case_id="case-x", units=[complete], conflicts=[], readiness_by_unit={}, incident_police_ready=False)
    # With no readiness, will go to missing or police
    assert act2.code in {"CONFIRM_TRANSACTION_AMOUNT", "PREPARE_POLICE_INCIDENT", "CONTACT_BANK_PJP"}
