from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr, png_bytes
from tests.hero_support import CHAT, TRANSFER, confirm_critical, create_case, resolve_amount_conflict, upload_text_png


def test_intake_lists_and_deletes_evidence(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    page = client.get(f"/cases/{case_id}/intake")
    assert page.status_code == 200
    assert "chat.png" in page.text
    assert "Hapus" in page.text
    ev = client.get(f"/api/v1/cases/{case_id}/evidence").json()["evidence"][0]["evidence_id"]
    gone = client.post(f"/cases/{case_id}/evidence/{ev}/delete", follow_redirects=False)
    assert gone.status_code == 303
    left = client.get(f"/api/v1/cases/{case_id}/evidence").json()["evidence"]
    assert left == []


def test_kasus_baru_purges_and_returns_home(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    data = png_bytes(CHAT)
    client.post(f"/api/v1/cases/{case_id}/evidence", files=[("files", ("chat.png", data, "image/png"))])
    res = client.post(f"/cases/{case_id}/baru", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/"
    missing = client.get(f"/api/v1/cases/{case_id}")
    assert missing.status_code in {403, 404, 410}


def test_buat_paket_form_is_clickable(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "run-pack"})
    resolve_amount_conflict(client, case_id)
    confirm_critical(client, case_id)
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    snap = draft.json()["snapshot_hash"]
    page = client.get(f"/cases/{case_id}/approval")
    assert page.status_code == 200
    assert "Buat paket" in page.text
    assert 'id="go" disabled' not in page.text
    denied = client.post(
        f"/cases/{case_id}/approval",
        data={"snapshot_hash": snap, "accepted_notice": ""},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] == "WAITING_APPROVAL"
    ok = client.post(
        f"/cases/{case_id}/approval",
        data={"snapshot_hash": snap, "accepted_notice": "1"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == f"/cases/{case_id}/artifacts"
    assert client.get(f"/api/v1/cases/{case_id}").json()["state"] in {
        "GENERATING",
        "VERIFYING",
        "HANDOFF_READY",
    }
