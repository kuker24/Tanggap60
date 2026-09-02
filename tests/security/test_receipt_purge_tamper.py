from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.policies import sha256_bytes
from tests.conftest import ScriptedOcr, png_bytes


def _ready_pack(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"}).json()[
        "case_id"
    ]
    text = "Transfer Rp2.750.000 Ke: DEMO-DEST-01 23 September 2026 08:42 WIB"
    data = png_bytes(text)
    ocr.by_hash[sha256_bytes(data)] = text
    client.post(f"/api/v1/cases/{case_id}/evidence", files=[("files", ("transfer.png", data, "image/png"))])
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "r1"})
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        if fact["review_status"] == "CANDIDATE":
            client.patch(
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    snap = draft.json()["snapshot_hash"]
    client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "a1"},
        json={"snapshot_hash": snap, "accepted_notice": True},
    )
    return case_id


def test_t15_t16_receipt(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ready_pack(client, ocr)
    match = client.post(
        f"/api/v1/cases/{case_id}/receipt",
        headers={"Idempotency-Key": "rc-1"},
        json={"ticket_text": "IASC123456", "ocr_text": "IASC123456"},
    )
    assert match.status_code == 200
    body = match.json()
    assert body["local_match_status"] == "MATCH"
    assert body["official_status"] == "NOT_VERIFIED"
    case_id2 = _ready_pack(client, ocr)
    mismatch = client.post(
        f"/api/v1/cases/{case_id2}/receipt",
        headers={"Idempotency-Key": "rc-2"},
        json={"ticket_text": "IASC123456", "ocr_text": "OTHER999999"},
    )
    assert mismatch.status_code == 200
    assert mismatch.json()["local_match_status"] == "MISMATCH"
    assert mismatch.json()["official_status"] == "NOT_VERIFIED"
    state = client.get(f"/api/v1/cases/{case_id2}").json()["state"]
    assert state != "COMPLETE"


def test_t18_purge(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    case_id = _ready_pack(client, ocr)
    res = client.request("DELETE", f"/api/v1/cases/{case_id}", json={"confirmation": "PURGE"})
    assert res.status_code == 200
    missing = client.get(f"/api/v1/cases/{case_id}")
    assert missing.status_code in {403, 404, 410}


def test_t11_tamper_blocks_download(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    case_id = _ready_pack(client, ocr)
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    json_art = next(a for a in arts if a["type"] == "CASE_JSON")
    _settings, _ocr, container = tmp_env
    session = container.sessions()
    from app.infrastructure.repositories import ArtifactRepository

    record = ArtifactRepository(session).get(json_art["artifact_id"])
    path = container.storage.path_for(case_id, record.storage_key)
    path.write_bytes(b'{"tampered": true}')
    session.close()
    dl = client.get(f"/api/v1/cases/{case_id}/artifacts/{json_art['artifact_id']}/download")
    assert dl.status_code in {409, 400, 500} or (dl.status_code == 200 and False)
    verify = client.post(f"/api/v1/cases/{case_id}/artifacts/verify")
    assert verify.status_code == 409
