from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import CHAT, TRANSFER, approve_to_handoff, create_case, resolve_amount_conflict, upload_text_png


def test_readiness_trace_has_no_raw_ocr(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "sec-1"})
    resolve_amount_conflict(client, case_id)
    approve_to_handoff(client, case_id)
    body = client.get(f"/api/v1/cases/{case_id}/trace").json()
    blob = str(body)
    assert "Rp2.500.000" not in blob
    assert "Rp2.750.000" not in blob
    assert "DEMO-DEST-01" not in blob
    ready = client.get(f"/api/v1/cases/{case_id}/readiness").json()
    ready_blob = str(ready)
    assert "Rp2.500.000" not in ready_blob
    assert "Kirim dulu" not in ready_blob
    assert ready["official_status"] == "NOT_VERIFIED"


def test_readiness_unknown_case_and_no_fetch(client: TestClient) -> None:
    missing = client.get("/api/v1/cases/case-does-not-exist/readiness")
    assert missing.status_code in {403, 404}
    case_id = create_case(client)
    ready = client.get(f"/api/v1/cases/{case_id}/readiness")
    assert ready.status_code == 200
    assert "http://" not in str(ready.json().get("channels"))


def test_tampered_channel_pack_fails(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "sec-tamper"})
    resolve_amount_conflict(client, case_id)
    approve_to_handoff(client, case_id)
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    pack = next(a for a in arts if a["type"] == "IASC_HANDOFF_PACK")
    _settings, _ocr, container = tmp_env
    session = container.sessions()
    from app.infrastructure.repositories import ArtifactRepository

    record = ArtifactRepository(session).get(pack["artifact_id"])
    path = container.storage.path_for(case_id, record.storage_key)
    data = bytearray(path.read_bytes())
    data[20] ^= 0x01
    path.write_bytes(bytes(data))
    session.close()
    verify = client.post(f"/api/v1/cases/{case_id}/artifacts/verify")
    assert verify.status_code == 409
