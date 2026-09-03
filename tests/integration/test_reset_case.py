from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr, png_bytes
from tests.hero_support import CHAT, create_case, upload_text_png


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
