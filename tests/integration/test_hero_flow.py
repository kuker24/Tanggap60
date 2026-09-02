from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import (
    CHAT,
    TRANSFER,
    approve_to_handoff,
    create_case,
    resolve_amount_conflict,
    tool_names,
    upload_text_png,
)


def test_t01_t04_hero(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "run-1"})
    assert run.status_code == 202
    body = run.json()
    assert body["state"] == "REVIEW_REQUIRED"
    assert "inspect_evidence" in body["trace"]
    assert "extract_candidate_facts" in body["trace"]
    conflicts = client.get(f"/api/v1/cases/{case_id}/conflicts").json()["conflicts"]
    open_blocking = [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    assert open_blocking, "T01 conflict required"
    locators = [f["source_locator"] for f in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]]
    assert all(locators) and any(str(loc).startswith("p") for loc in locators)
    html = client.get(f"/cases/{case_id}/review").text
    assert "Gunakan" in html
    assert "2.500.000" in html or "Rp2.500.000" in html
    approval = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "bad-appr"},
        json={"snapshot_hash": "0" * 64, "accepted_notice": True},
    )
    assert approval.status_code == 409
    assert approval.json()["code"] in {"OPEN_CONFLICTS", "APPROVAL_REQUIRED", "APPROVAL_HASH_MISMATCH"}
    resolve_amount_conflict(client, case_id)
    ok = approve_to_handoff(client, case_id)
    assert ok["state"] == "HANDOFF_READY"
    tools = tool_names(client, case_id)
    assert "prepare_official_handoff" in tools
    assert "verify_artifacts" in tools
    assert "compile_artifacts" in tools
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    assert arts
    assert all(a["verify_status"] == "PASS" for a in arts)
    steps = {s["tool_name"]: s for s in client.get(f"/api/v1/cases/{case_id}/trace").json()["steps"]}
    assert steps["compile_artifacts"]["planner"] == "DETERMINISTIC_SAFE"
    assert steps["verify_artifacts"]["planner"] == "DETERMINISTIC_SAFE"
    assert steps["inspect_evidence"]["planner"] == "DETERMINISTIC_SAFE"
    fact_id = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"][0]["fact_id"]
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    patched = client.patch(
        f"/api/v1/cases/{case_id}/facts/{fact_id}",
        json={"action": "confirm", "expected_version": version},
    )
    assert patched.status_code == 200
    state = client.get(f"/api/v1/cases/{case_id}").json()["state"]
    assert state == "REVIEW_REQUIRED"


def test_cross_session_denied(client: TestClient) -> None:
    case_id = create_case(client)
    other = TestClient(client.app)
    res = other.get(f"/api/v1/cases/{case_id}")
    assert res.status_code == 403
