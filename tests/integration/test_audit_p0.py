from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import (
    CHAT,
    TRANSFER,
    approve_to_handoff,
    confirm_critical,
    create_case,
    resolve_amount_conflict,
    upload_text_png,
)


def test_before_loss_chat_amount_stays_preincident(client: TestClient, ocr: ScriptedOcr) -> None:
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"})
    case_id = created.json()["case_id"]
    assert created.json()["route"] == "PRE_INCIDENT_CHECK"
    client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"text": CHAT})
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "pre-chat"})
    assert run.status_code == 202
    body = client.get(f"/api/v1/cases/{case_id}").json()
    assert body["route"] == "PRE_INCIDENT_CHECK"
    assert body["ask_loss_question"] is True
    review = client.get(f"/cases/{case_id}/review")
    assert review.status_code == 200
    assert "Uangnya sudah terkirim?" in review.text


def test_review_shows_only_one_pending_fact(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "a.png", "Transfer Rp1.000.000 Ke: DEMO-DEST-A 4 Januari 2024 09:10 WIB")
    upload_text_png(client, ocr, case_id, "b.png", "Transfer Rp1.000.000 Ke: DEMO-DEST-B 5 Januari 2024 10:10 WIB")
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "dup-amt"})
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    candidates = [f for f in facts if f["review_status"] == "CANDIDATE"]
    assert len(candidates) >= 2
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    assert sum(fact["fact_id"] in page.text for fact in candidates) == 1
    assert f"1 dari {len(candidates)} data yang belum dicek" in page.text


def test_add_chat_after_review_does_not_500(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "then-chat"})
    confirm_critical(client, case_id)
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    added = client.post(
        f"/cases/{case_id}/intake",
        data={"text": CHAT},
        follow_redirects=False,
    )
    assert added.status_code in {303, 200}
    follow = client.get(added.headers.get("location", f"/cases/{case_id}/processing"))
    assert follow.status_code != 500
    assert "Sedang ada gangguan" not in follow.text


def test_revoked_pack_is_not_active_download(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "rev-pack"})
    resolve_amount_conflict(client, case_id)
    approve_to_handoff(client, case_id)
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    zip_art = next(a for a in arts if a["type"] == "CASE_ZIP")
    ok = client.get(f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download")
    assert ok.status_code == 200
    revoked = client.delete(f"/api/v1/cases/{case_id}/approval")
    assert revoked.status_code == 200
    denied = client.get(f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download")
    assert denied.status_code >= 400


def test_bad_url_is_validation_not_500(client: TestClient) -> None:
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"})
    case_id = created.json()["case_id"]
    res = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"url": "https://example.com:abc"})
    assert res.status_code != 500
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "bad-url"})
    assert run.status_code != 500


def test_kelola_data_and_intake_copy(client: TestClient) -> None:
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/intake")
    assert page.status_code == 200
    assert "Kelola data" in page.text
    assert "Satu bukti saja cukup untuk mulai" in page.text
    assert "(boleh kosong)" not in page.text


def test_review_cta_lihat_ringkasan(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "t.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "cta-rev"})
    confirm_critical(client, case_id)
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    assert "Lihat langkah berikutnya" in page.text
    assert "Butuh bantuan?" in page.text


def test_review_continue_builds_preincident_result(client: TestClient) -> None:
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"})
    case_id = created.json()["case_id"]
    client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"url": "https://iasc.ojk.go.id.evil.example/a"})
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "web-continue-pre"})
    fact = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"][0]
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    confirmed = client.patch(
        f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
        json={"action": "confirm", "expected_version": version},
    )
    assert confirmed.status_code == 200
    continued = client.post(f"/cases/{case_id}/continue", follow_redirects=False)
    assert continued.status_code == 303
    assert continued.headers["location"] == f"/cases/{case_id}/result"
    result = client.get(continued.headers["location"])
    assert result.status_code == 200
    assert "Kami hanya memeriksa bentuk link" in result.text


def test_help_button_is_in_header(client: TestClient) -> None:
    case_id = create_case(client)
    html = client.get(f"/cases/{case_id}/intake").text
    assert html.index('id="agent-fab"') < html.index('id="main"')
    assert html.index('id="main"') < html.index('id="agent-panel"')
    assert "top-actions" in html
    assert "Bantuan Tanggap60" in html
    assert 'aria-expanded="false"' in html
    assert "Panduan langsung" in html
    assert 'id="agent-autopilot" type="checkbox">' in html


def test_ocr_does_not_hold_sqlite_write_lock(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    _settings, _ocr, container = tmp_env
    blocked = False
    orig = ocr.recognize

    def probe(data: bytes) -> str:
        nonlocal blocked
        raw = container.engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("PRAGMA busy_timeout=0")
            cur.execute("BEGIN IMMEDIATE")
            raw.commit()
        except Exception:
            blocked = True
        finally:
            raw.close()
        return orig(data)

    ocr.recognize = probe  # type: ignore[method-assign]
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "lock.png", TRANSFER)
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "lock-ocr"})
    assert run.status_code == 202
    assert blocked is False


def test_before_loss_intake_defaults_to_chat_tab(client: TestClient) -> None:
    started = client.post("/start", data={"declared_condition": "BEFORE_LOSS", "mode": "DEMO"}, follow_redirects=False)
    page = client.get(started.headers["location"])
    assert page.status_code == 200
    assert 'data-default-tab="text"' in page.text


def test_review_lists_one_candidate_at_a_time(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "t.png", TRANSFER)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "all-facts"})
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    candidates = [f for f in facts if f["review_status"] == "CANDIDATE"]
    assert sum(f["fact_id"] in page.text for f in candidates) == 1
    assert f"1 dari {len(candidates)} data yang belum dicek" in page.text


def test_foreign_case_html_does_not_claim_deleted(client: TestClient) -> None:
    case_id = create_case(client)
    other = TestClient(client.app)
    page = other.get(f"/cases/{case_id}/intake", headers={"Accept": "text/html"})
    assert page.status_code in {403, 404}
    assert "Kasus tidak bisa dibuka" in page.text
    assert "Data kasus demo sudah" not in page.text
    assert "dibuat di perangkat lain" in page.text
    assert "data sudah hilang" not in page.text


def test_recover_stale_fails_after_attempt_budget(tmp_env) -> None:
    from datetime import timedelta

    from app.infrastructure.db import JobRow
    from app.infrastructure.jobs import JobQueue
    from app.services.cases import now_utc

    _settings, _ocr, container = tmp_env
    session = container.sessions()
    queue = JobQueue(session)
    job_id = queue.enqueue(case_id="case-x", run_id="run-x", kind="orchestrate", idempotency_key="k-x")
    claimed = queue.claim_next()
    assert claimed is not None
    claimed.attempts = 3
    claimed.started_at = now_utc() - timedelta(seconds=700)
    session.commit()
    count = JobQueue(session).recover_stale()
    session.commit()
    row = session.get(JobRow, job_id)
    session.close()
    assert count == 1
    assert row is not None
    assert row.status == "failed"
