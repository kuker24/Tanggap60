from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_empty_review_and_approval_do_not_pretend_progress(client: TestClient) -> None:
    case_id = create_case(client)
    review = client.get(f"/cases/{case_id}/review")
    assert review.status_code == 200
    assert "Belum ada data" in review.text
    assert "Tambah bukti" in review.text
    assert "Lanjut dulu" not in review.text
    assert "Buat paket untuk 0" not in review.text
    assert 'class="step done"' not in review.text

    approval = client.get(f"/cases/{case_id}/approval")
    assert approval.status_code == 200
    assert "Belum ada data" in approval.text
    assert "Buat paket untuk 0" not in approval.text
    assert 'id="approval-form"' not in approval.text
    assert 'name="accepted_notice"' not in approval.text
    assert "Tambah bukti" in approval.text

    ready = client.get(f"/cases/{case_id}/readiness")
    assert ready.status_code == 200
    assert "Belum ada data untuk diperiksa" in ready.text
    assert ready.text.count("Tambah bukti") >= 1
    assert "Lanjut dulu" not in ready.text
    assert "Buat paket untuk 0" not in ready.text


def test_intake_tabs_and_path_titles(client: TestClient) -> None:
    after = create_case(client)
    page = client.get(f"/cases/{after}/intake")
    assert "Kirim bukti yang ada" in page.text
    assert "Teks chat" in page.text
    assert ">Link<" in page.text
    assert "Saya tidak punya file" not in page.text

    started = client.post("/start", data={"declared_condition": "BEFORE_LOSS", "mode": "DEMO"}, follow_redirects=False)
    assert started.status_code == 303
    before_page = client.get(started.headers["location"])
    assert before_page.status_code == 200
    assert "Cek chat atau link" in before_page.text


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
    assert "Belum ada data untuk disalin" in page.text


def test_postincident_link_only_asks_for_transaction_evidence(client: TestClient) -> None:
    case_id = create_case(client)
    added = client.post(
        f"/api/v1/cases/{case_id}/evidence/text",
        json={"url": "https://example.com/offer"},
    )
    assert added.status_code == 202
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "link-only"})
    assert run.status_code == 202

    fact = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"][0]
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    confirmed = client.patch(
        f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
        json={"action": "correct", "value": "https://example.com/", "expected_version": version},
    )
    assert confirmed.status_code == 200

    review = client.get(f"/cases/{case_id}/review")
    assert "Data yang ditemukan sudah Anda cek" in review.text
    assert "Tambah bukti transaksi" in review.text
    assert "Semua data sudah Anda cek" not in review.text
    assert "Lihat langkah berikutnya" not in review.text

    continued = client.post(f"/cases/{case_id}/continue", follow_redirects=False)
    assert continued.status_code == 303
    assert continued.headers["location"] == f"/cases/{case_id}/intake?notice=butuh-transaksi"
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    assert draft.json()["state"] == "REVIEW_REQUIRED"

    readiness = client.get(f"/cases/{case_id}/readiness")
    assert "Tambah bukti transaksi" in readiness.text
    assert "Ada data yang perlu Anda cek sebelum membuat dokumen" not in readiness.text
    intake = client.get(continued.headers["location"])
    assert "Bukti awal sudah tersimpan" in intake.text
    assert "bukti transfer atau teks chat" in intake.text
