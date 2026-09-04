from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
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


def _pair(tmp_env) -> tuple[TestClient, TestClient, ScriptedOcr]:
    _settings, ocr, container = tmp_env
    app = create_app(container)
    return TestClient(app), TestClient(app), ocr


def test_cannot_delete_foreign_evidence(tmp_env) -> None:
    owner, attacker, ocr = _pair(tmp_env)
    victim = create_case(owner)
    attacker_case = create_case(attacker)
    owner.post(f"/api/v1/cases/{victim}/evidence/text", json={"text": "bukti pemilik"})
    ev = owner.get(f"/api/v1/cases/{victim}/evidence").json()["evidence"][0]["evidence_id"]
    denied = attacker.delete(f"/api/v1/cases/{attacker_case}/evidence/{ev}")
    assert denied.status_code in {403, 404}
    left = owner.get(f"/api/v1/cases/{victim}/evidence").json()["evidence"]
    assert len(left) == 1
    assert left[0]["evidence_id"] == ev


def test_cannot_resolve_foreign_conflict(tmp_env) -> None:
    owner, attacker, ocr = _pair(tmp_env)
    victim = create_case(owner)
    upload_text_png(owner, ocr, victim, "chat.png", CHAT)
    upload_text_png(owner, ocr, victim, "transfer.png", TRANSFER)
    owner.post(f"/api/v1/cases/{victim}/runs", headers={"Idempotency-Key": "own-run"})
    conflict = owner.get(f"/api/v1/cases/{victim}/conflicts").json()["conflicts"][0]
    attacker_case = create_case(attacker)
    upload_text_png(attacker, ocr, attacker_case, "chat.png", CHAT)
    upload_text_png(attacker, ocr, attacker_case, "transfer.png", TRANSFER)
    attacker.post(f"/api/v1/cases/{attacker_case}/runs", headers={"Idempotency-Key": "atk-run"})
    version = attacker.get(f"/api/v1/cases/{attacker_case}").json()["version"]
    denied = attacker.post(
        f"/api/v1/cases/{attacker_case}/conflicts/{conflict['conflict_id']}/resolve",
        json={"resolution_fact_id": conflict["fact_ids"][0], "expected_version": version},
    )
    assert denied.status_code in {400, 403, 404}
    still = owner.get(f"/api/v1/cases/{victim}/conflicts").json()["conflicts"]
    open_blocking = [c for c in still if c["conflict_id"] == conflict["conflict_id"] and c["status"] == "OPEN"]
    assert open_blocking


def test_receipt_requires_owner_session(tmp_env) -> None:
    owner, attacker, ocr = _pair(tmp_env)
    case_id = create_case(owner)
    upload_text_png(owner, ocr, case_id, "chat.png", CHAT)
    upload_text_png(owner, ocr, case_id, "transfer.png", TRANSFER)
    owner.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "rcpt-run"})
    resolve_amount_conflict(owner, case_id)
    approve_to_handoff(owner, case_id)
    denied = attacker.post(
        f"/api/v1/cases/{case_id}/receipt",
        headers={"Idempotency-Key": "stolen"},
        json={"ticket_text": "IASC123456"},
    )
    assert denied.status_code in {403, 404}
    assert owner.get(f"/api/v1/cases/{case_id}/receipt").json().get("ticket_value_masked") in {None, ""}


def test_idempotent_replay_requires_owner(tmp_env) -> None:
    owner, attacker, ocr = _pair(tmp_env)
    case_id = create_case(owner)
    upload_text_png(owner, ocr, case_id, "chat.png", CHAT)
    upload_text_png(owner, ocr, case_id, "transfer.png", TRANSFER)
    owner.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "idem-run"})
    resolve_amount_conflict(owner, case_id)
    confirm_critical(owner, case_id)
    owner.post(f"/api/v1/cases/{case_id}/draft")
    snap = owner.post(f"/api/v1/cases/{case_id}/draft").json()["snapshot_hash"]
    payload = {"snapshot_hash": snap, "accepted_notice": True}
    ok = owner.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "appr-shared"},
        json=payload,
    )
    assert ok.status_code == 200
    replay = attacker.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "appr-shared"},
        json=payload,
    )
    assert replay.status_code in {403, 404}
