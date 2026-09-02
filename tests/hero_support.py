from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.policies import sha256_bytes
from tests.conftest import ScriptedOcr, png_bytes

CHAT = "Kirim dulu Rp2.500.000 ke rekening ini ya biar pesanan diproses"
TRANSFER = "Transfer Berhasil Rp2.750.000 Ke: DEMO-DEST-01 23 September 2026 08:42 WIB Dari: DEMO-VICTIM-MASKED"


def create_case(client: TestClient) -> str:
    res = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    assert res.status_code == 201
    return res.json()["case_id"]


def upload_text_png(client: TestClient, ocr: ScriptedOcr, case_id: str, name: str, text: str) -> None:
    data = png_bytes(text)
    ocr.by_hash[sha256_bytes(data)] = text
    res = client.post(f"/api/v1/cases/{case_id}/evidence", files=[("files", (name, data, "image/png"))])
    assert res.status_code == 202


def confirm_critical(client: TestClient, case_id: str) -> None:
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        if fact["criticality"] == "CRITICAL" and fact["review_status"] == "CANDIDATE":
            client.patch(
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]


def resolve_amount_conflict(client: TestClient, case_id: str, winner_contains: str = "2.750") -> None:
    conflicts = client.get(f"/api/v1/cases/{case_id}/conflicts").json()["conflicts"]
    open_blocking = [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    assert open_blocking
    facts = {f["fact_id"]: f for f in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]}
    fact_ids = open_blocking[0]["fact_ids"]
    winner = next(
        fid
        for fid in fact_ids
        if winner_contains in facts[fid]["raw_value"] or facts[fid]["normalized_value"] == "2750000"
    )
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    res = client.post(
        f"/api/v1/cases/{case_id}/conflicts/{open_blocking[0]['conflict_id']}/resolve",
        json={"resolution_fact_id": winner, "expected_version": version},
    )
    assert res.status_code == 200


def approve_to_handoff(client: TestClient, case_id: str) -> dict:
    confirm_critical(client, case_id)
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    snap = draft.json()["snapshot_hash"]
    ok = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": f"appr-{case_id[-8:]}"},
        json={"snapshot_hash": snap, "accepted_notice": True},
    )
    assert ok.status_code == 200, ok.text
    return ok.json()


def tool_names(client: TestClient, case_id: str) -> list[str]:
    events = client.get(f"/api/v1/cases/{case_id}/events").json()["events"]
    return [e["tool_name"] for e in events if e.get("tool_name")]
