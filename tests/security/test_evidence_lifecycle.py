from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import (
    CHAT,
    TRANSFER,
    confirm_critical,
    create_case,
    resolve_amount_conflict,
    upload_text_png,
)


def test_waiting_approval_accepts_new_evidence(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "life-run"})
    resolve_amount_conflict(client, case_id)
    confirm_critical(client, case_id)
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] == "WAITING_APPROVAL"
    added = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"text": "bukti tambahan sebelum paket"})
    assert added.status_code == 202
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] == "INGESTING"


def test_review_required_new_evidence_is_extracted(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "life-rev"})
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] == "REVIEW_REQUIRED"
    before = {f["fact_id"] for f in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]}
    extra = "Transfer Berhasil Rp100.000 Ke: DEMO-DEST-EXTRA 23 September 2026 11:11 WIB"
    upload_text_png(client, ocr, case_id, "extra.png", extra)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "life-rev-2"})
    after = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    assert any(f["fact_id"] not in before and "DEST-EXTRA" in f["raw_value"] for f in after)


def test_extracted_evidence_cannot_be_deleted(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "life-del"})
    ev = client.get(f"/api/v1/cases/{case_id}/evidence").json()["evidence"][0]
    assert ev["status"] == "EXTRACTED"
    denied = client.delete(f"/api/v1/cases/{case_id}/evidence/{ev['evidence_id']}")
    assert denied.status_code == 409
    left = client.get(f"/api/v1/cases/{case_id}/evidence").json()["evidence"]
    assert len(left) == 1
