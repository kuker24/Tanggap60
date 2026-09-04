from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_empty_review_and_approval_do_not_pretend_progress(client: TestClient) -> None:
    case_id = create_case(client)
    review = client.get(f"/cases/{case_id}/review")
    assert review.status_code == 200
    assert "Belum ada transaksi untuk diperiksa" in review.text
    assert "Periksa bukti" in review.text
    assert "Lanjut dulu" not in review.text
    assert "Buat paket untuk 0" not in review.text
    assert 'class="step done"' not in review.text

    approval = client.get(f"/cases/{case_id}/approval")
    assert approval.status_code == 200
    assert "Belum ada transaksi untuk diperiksa" in approval.text
    assert "Buat paket untuk 0" not in approval.text
    assert "Buat dokumen" not in approval.text
    assert 'name="accepted_notice"' not in approval.text
    assert "Periksa bukti" in approval.text

    ready = client.get(f"/cases/{case_id}/readiness")
    assert ready.status_code == 200
    assert "Belum ada transaksi untuk diperiksa" in ready.text
    assert ready.text.count("Periksa bukti") >= 1
    assert "Lanjut dulu" not in ready.text
    assert "Buat paket untuk 0" not in ready.text


def test_intake_tabs_and_path_titles(client: TestClient) -> None:
    after = create_case(client)
    page = client.get(f"/cases/{after}/intake")
    assert "Kumpulkan bukti transaksi" in page.text
    assert "Tempel Chat" in page.text
    assert "Masukkan Link" in page.text
    assert "Saya tidak punya file" not in page.text

    started = client.post("/start", data={"declared_condition": "BEFORE_LOSS", "mode": "DEMO"}, follow_redirects=False)
    assert started.status_code == 303
    before_page = client.get(started.headers["location"])
    assert before_page.status_code == 200
    assert "Periksa chat yang mencurigakan" in before_page.text


def test_empty_workspace_log_has_no_fake_work(client: TestClient) -> None:
    case_id = create_case(client)
    ws = client.get(f"/api/v1/cases/{case_id}/workspace")
    assert ws.status_code == 200
    body = ws.json()
    assert body["confirmed_transactions"] == 0
    assert all("menyiapkan 0" not in step for step in body["action_log"])
    assert not any("menyusun kronologi" in step for step in body["action_log"])

    page = client.get(f"/cases/{case_id}/workspace")
    assert page.status_code == 200
    assert "Belum ada yang bisa disiapkan" in page.text
