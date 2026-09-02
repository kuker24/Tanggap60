from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.policies import sha256_bytes
from tests.conftest import ScriptedOcr, png_bytes


def _create(client: TestClient) -> str:
    res = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    assert res.status_code == 201
    return res.json()["case_id"]


def _upload(client: TestClient, ocr: ScriptedOcr, case_id: str, name: str, text: str) -> None:
    data = png_bytes(text)
    ocr.by_hash[sha256_bytes(data)] = text
    res = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[("files", (name, data, "image/png"))],
    )
    assert res.status_code == 202


def test_t01_t04_hero(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _create(client)
    _upload(client, ocr, case_id, "chat.png", "Kirim dulu Rp2.500.000 ke rekening ini ya biar pesanan diproses")
    _upload(
        client,
        ocr,
        case_id,
        "transfer.png",
        "Transfer Berhasil Rp2.750.000 Ke: DEMO-DEST-01 23 September 2026 08:42 WIB Dari: DEMO-VICTIM-MASKED",
    )
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "run-1"})
    assert run.status_code == 202
    body = run.json()
    assert body["state"] == "REVIEW_REQUIRED"
    assert "inspect_evidence" in body["trace"]
    assert "extract_candidate_facts" in body["trace"]
    conflicts = client.get(f"/api/v1/cases/{case_id}/conflicts").json()["conflicts"]
    open_blocking = [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    assert open_blocking, "T01 conflict required"
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    approval = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "bad-appr"},
        json={"snapshot_hash": "0" * 64, "accepted_notice": True},
    )
    assert approval.status_code == 409
    assert approval.json()["code"] in {"OPEN_CONFLICTS", "APPROVAL_REQUIRED", "APPROVAL_HASH_MISMATCH"}
    fact_ids = open_blocking[0]["fact_ids"]
    facts = {f["fact_id"]: f for f in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]}
    winner = next(fid for fid in fact_ids if "2.750" in facts[fid]["raw_value"] or facts[fid]["normalized_value"] == "2750000")
    resolved = client.post(
        f"/api/v1/cases/{case_id}/conflicts/{open_blocking[0]['conflict_id']}/resolve",
        json={"resolution_fact_id": winner, "expected_version": version},
    )
    assert resolved.status_code == 200
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        if fact["criticality"] == "CRITICAL" and fact["review_status"] == "CANDIDATE":
            client.patch(
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    assert draft.json()["state"] == "WAITING_APPROVAL"
    snap = draft.json()["snapshot_hash"]
    ok = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "appr-1"},
        json={"snapshot_hash": snap, "accepted_notice": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "HANDOFF_READY"
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    assert arts
    assert all(a["verify_status"] == "PASS" for a in arts)
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
    case_id = _create(client)
    other = TestClient(client.app)
    res = other.get(f"/api/v1/cases/{case_id}")
    assert res.status_code == 403
