from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.errors import ValidationFailed
from app.domain.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    Criticality,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    FactRecord,
    FactType,
    ReviewStatus,
    TransactionGroupRecord,
)
from app.domain.policies import canonical_json, sha256_text, snapshot_payload
from app.domain.states import Route
from app.hermes.adapter import DeterministicHermes
from app.services.readiness import assess, load_profile, snapshot_readiness, validate_profile

NOW = datetime(2026, 9, 2, tzinfo=UTC)
HASH = "a" * 64


def _fact(fid: str, ftype: FactType, value: str, evid: str, status: ReviewStatus = ReviewStatus.CONFIRMED) -> FactRecord:
    return FactRecord(
        fact_id=fid,
        case_id="case-x",
        type=ftype,
        raw_value=value,
        normalized_value=value,
        criticality=Criticality.CRITICAL if ftype in {FactType.AMOUNT, FactType.ACCOUNT, FactType.DATETIME} else Criticality.IMPORTANT,
        confidence=0.9,
        review_status=status,
        source_evidence_id=evid,
        source_page=1,
        source_bbox="p1:1-8",
        source_excerpt_hash=HASH,
    )


def _ev(eid: str, name: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=eid,
        case_id="case-x",
        kind=EvidenceKind.IMAGE,
        original_name_display=name,
        storage_key=eid,
        mime="image/png",
        size_bytes=12,
        sha256=HASH,
        page_count=1,
        status=EvidenceStatus.EXTRACTED,
        retention_until=NOW,
    )


def _complete() -> dict[str, object]:
    facts = [
        _fact("f-amt", FactType.AMOUNT, "2750000", "ev-t"),
        _fact("f-acc", FactType.ACCOUNT, "DEMO-DEST-01", "ev-t"),
        _fact("f-time", FactType.DATETIME, "2026-09-23T01:42:00Z", "ev-t"),
        _fact("f-pjp", FactType.PJP, "Bank Demo", "ev-t", ReviewStatus.CONFIRMED),
        _fact("f-claim", FactType.CLAIM, "diminta transfer", "ev-c"),
    ]
    evidence = [_ev("ev-c", "chat.png"), _ev("ev-t", "transfer.png")]
    tx = [
        TransactionGroupRecord(
            transaction_group_id="tx-1",
            case_id="case-x",
            victim_account=None,
            destination_account="DEMO-DEST-01",
            amount=2750000,
            transferred_at="2026-09-23T01:42:00Z",
            evidence_ids=["ev-t"],
            readiness="READY",
        )
    ]
    return {"facts": facts, "evidence": evidence, "transactions": tx, "conflicts": []}


def test_profile_loads() -> None:
    profile = load_profile()
    assert profile["profile_version"] == "2026-09-02.mvp2"


def test_ready_complete_fixture() -> None:
    data = _complete()
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    assert report["overall_status"] == "READY"
    assert all(ch["status"] == "READY" for ch in report["channels"])
    assert report["official_status"] == "NOT_VERIFIED"


def test_needs_action_without_time() -> None:
    data = _complete()
    data["facts"] = [f for f in data["facts"] if f.type != FactType.DATETIME]
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    assert report["overall_status"] == "NEEDS_ACTION"
    bank = next(ch for ch in report["channels"] if ch["channel"] == "BANK_PJP")
    time_check = next(c for c in bank["checks"] if c["check_id"] == "BANK_TIME_REVIEWED")
    assert time_check["status"] == "MISSING"


def test_blocked_open_conflict() -> None:
    data = _complete()
    data["conflicts"] = [
        ConflictRecord(
            conflict_id="cf-1",
            case_id="case-x",
            type=ConflictType.VALUE_MISMATCH,
            fact_ids=["f-amt", "f-amt2"],
            severity=ConflictSeverity.BLOCKING,
            status=ConflictStatus.OPEN,
        )
    ]
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    assert report["overall_status"] == "BLOCKED"


def test_provenance_points_at_evidence() -> None:
    data = _complete()
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    bank = next(ch for ch in report["channels"] if ch["channel"] == "BANK_PJP")
    amount = next(c for c in bank["checks"] if c["check_id"] == "BANK_AMOUNT_REVIEWED")
    assert amount["fact_ids"] == ["f-amt"]
    transfer = next(c for c in bank["checks"] if c["check_id"] == "BANK_TRANSFER_EVIDENCE")
    assert "ev-t" in transfer["evidence_ids"]


def test_prepare_externally_is_not_uploaded_evidence() -> None:
    data = _complete()
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    bank = next(ch for ch in report["channels"] if ch["channel"] == "BANK_PJP")
    ext = next(c for c in bank["checks"] if c["check_id"] == "BANK_IDENTITY_EXTERNAL")
    assert ext["status"] == "PREPARE_EXTERNALLY"
    assert ext["evidence_ids"] == []


def test_invalid_profile_fail_safe(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        load_profile(bad)
    with pytest.raises(ValidationFailed):
        validate_profile({"profile_version": "x"})


def test_snapshot_changes_when_readiness_changes() -> None:
    data = _complete()
    ready = snapshot_readiness(assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data))
    data["facts"] = [f for f in data["facts"] if f.type != FactType.DATETIME]
    missing = snapshot_readiness(assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data))
    left = snapshot_payload(facts=[], conflicts=[], route="POST_INCIDENT_RESPONSE", actions=[], notice_version="n", template_version="t", readiness=ready)
    right = snapshot_payload(facts=[], conflicts=[], route="POST_INCIDENT_RESPONSE", actions=[], notice_version="n", template_version="t", readiness=missing)
    assert sha256_text(canonical_json(left)) != sha256_text(canonical_json(right))


def test_snapshot_order_does_not_change_digest() -> None:
    data = _complete()
    report = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    first = snapshot_readiness(report)
    report["channels"] = list(reversed(report["channels"]))
    second = snapshot_readiness(report)
    assert canonical_json(first) == canonical_json(second)


def test_deterministic_same_input() -> None:
    data = _complete()
    a = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    b = assess(case_id="case-x", route=Route.POST_INCIDENT_RESPONSE, **data)
    assert json.dumps(snapshot_readiness(a), sort_keys=True) == json.dumps(snapshot_readiness(b), sort_keys=True)


def test_schema_2_0_still_valid() -> None:
    from pathlib import Path as P

    import jsonschema

    schema = json.loads((P(__file__).resolve().parents[2] / "schemas" / "case.schema.json").read_text())
    payload = {
        "schema_version": "2.0",
        "case_id": "case-old-20",
        "mode": "DEMO",
        "route": "POST_INCIDENT_RESPONSE",
        "state": "HANDOFF_READY",
        "facts": [],
        "conflicts": [],
        "actions": [],
        "artifacts": [],
        "official_status": "NOT_VERIFIED",
    }
    jsonschema.validate(payload, schema)


def test_deterministic_hermes_asks_assess_after_plan() -> None:
    hermes = DeterministicHermes()
    assert hermes.propose_tool("READY_FOR_ACTION", {"route": "POST_INCIDENT_RESPONSE"}) == "build_postincident_plan"
    assert (
        hermes.propose_tool(
            "READY_FOR_ACTION",
            {"route": "POST_INCIDENT_RESPONSE", "plan_done": True},
        )
        == "compile_reporting_units"
    )
    assert (
        hermes.propose_tool(
            "READY_FOR_ACTION",
            {"route": "POST_INCIDENT_RESPONSE", "plan_done": True, "units_compiled": True},
        )
        == "assess_handoff_readiness"
    )
    assert (
        hermes.propose_tool(
            "READY_FOR_ACTION",
            {"route": "POST_INCIDENT_RESPONSE", "plan_done": True, "units_compiled": True, "readiness_assessed": True},
        )
        == "recommend_next_action"
    )
