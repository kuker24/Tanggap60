from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import CHAT, TRANSFER, create_case, upload_text_png


def test_result_redirects_to_review_while_candidates_open(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "wait-1"})
    assert run.status_code == 202
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] == "REVIEW_REQUIRED"
    res = client.get(f"/cases/{case_id}/result", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].endswith(f"/cases/{case_id}/review")
