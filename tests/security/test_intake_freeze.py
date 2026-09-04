from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import (
    CHAT,
    TRANSFER,
    approve_to_handoff,
    create_case,
    resolve_amount_conflict,
    upload_text_png,
)


def _handoff_case(client: TestClient, ocr: ScriptedOcr, key: str) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": key})
    resolve_amount_conflict(client, case_id)
    ok = approve_to_handoff(client, case_id)
    assert ok["state"] == "HANDOFF_READY"
    return case_id


def test_text_blocked_after_approval(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _handoff_case(client, ocr, "frz-run")
    res = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"text": "tambahan setelah approve"})
    assert res.status_code == 409
    assert res.json()["code"] == "INVALID_STATE_TRANSITION"


def test_url_blocked_after_approval(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _handoff_case(client, ocr, "frz-url")
    res = client.post(
        f"/api/v1/cases/{case_id}/evidence/text",
        json={"url": "https://iasc.ojk.go.id/"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "INVALID_STATE_TRANSITION"


def test_ninth_text_hits_quota(client: TestClient) -> None:
    case_id = create_case(client)
    for i in range(8):
        res = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"text": f"bukti {i}"})
        assert res.status_code == 202
    res = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"text": "kelebihan"})
    assert res.status_code == 413
    assert res.json()["code"] == "UPLOAD_LIMIT_EXCEEDED"


def test_url_too_long(client: TestClient) -> None:
    case_id = create_case(client)
    res = client.post(
        f"/api/v1/cases/{case_id}/evidence/text",
        json={"url": "https://example.com/" + ("a" * 4096)},
    )
    assert res.status_code == 413
    assert res.json()["code"] == "UPLOAD_LIMIT_EXCEEDED"
