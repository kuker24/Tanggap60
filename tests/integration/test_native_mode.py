"""Native Action Mode: assist loop, prefill-vs-commit, voice approve/deny, pause/resume/stop."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import confirm_critical, create_case, upload_text_png
from tests.integration.test_agent_conversation import TWO_TX_AMBIGUOUS

CORRECTION = "Yang 750 ribu ke DEMO-DEST-B."


def _agent_case(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", TWO_TX_AMBIGUOUS)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "native-run"})
    confirm_critical(client, case_id)
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert any(u["mapping_status"] == "AMBIGUOUS" for u in units)
    return case_id


def _ask(client: TestClient, case_id: str, text: str, ui_state: dict | None = None) -> dict:
    res = client.post(f"/api/v1/cases/{case_id}/agent/messages", json={"text": text, "ui_state": ui_state or {}})
    assert res.status_code == 200, res.text
    return res.json()


def _plan_types(body: dict) -> list[str]:
    return [s["type"] for s in (body.get("guidance_plan") or [])]


def _ambiguous_units(client: TestClient, case_id: str) -> list[dict]:
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    return [u for u in units if u["mapping_status"] == "AMBIGUOUS"]


def test_assist_full_opens_transaction_natively(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Bantu saya sampai selesai.")
    types = _plan_types(body)
    assert "OPEN_TRANSACTION" in types
    assert "WAIT_FOR_USER" in types
    assert types.index("OPEN_TRANSACTION") < types.index("WAIT_FOR_USER")
    assert body["guidance"] and body["guidance"]["target"].startswith("transaction-ru_")
    assert body["voice_note"]


def test_open_tx_ordinal_picks_second(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    ordered = sorted(units, key=lambda u: (u.get("index", 0), u["unit_id"]))
    n = len(ordered)
    assert n >= 1
    if n >= 2:
        body = _ask(client, case_id, "Yang kedua.")
        want = ordered[1]["unit_id"]
    else:
        body = _ask(client, case_id, "Yang pertama.")
        want = ordered[0]["unit_id"]
    assert body["guidance"] is not None
    assert want in body["guidance"]["target"]
    assert "OPEN_TRANSACTION" in _plan_types(body)


def test_correction_prefills_without_server_commit(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    before = _ambiguous_units(client, case_id)
    body = _ask(client, case_id, CORRECTION)
    assert body["proposed_action"] is not None
    assert body["proposed_action"]["risk"] == "YELLOW"
    assert "SET_DRAFT" in _plan_types(body)
    # PREFILL != COMMIT: server belum berubah sebelum approval.
    after = _ambiguous_units(client, case_id)
    assert [u["unit_id"] for u in after] == [u["unit_id"] for u in before]


def test_voice_yes_approves_and_continues(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    target_uid = prop["payload"]["unit_id"]
    saved = _ask(client, case_id, "Iya.", {"voice": True, "pending_action": prop, "current_page": "review"})
    assert saved["proposed_action"] is None
    assert saved["draft_committed"] is True
    remaining = [u["unit_id"] for u in _ambiguous_units(client, case_id)]
    assert target_uid not in remaining
    assert saved["guidance_plan"]  # agentic loop: rencana berikut langsung diberikan
    trail = client.get(f"/api/v1/cases/{case_id}/agent/trail").json()["trail"]
    kinds = {e["event_type"] for e in trail}
    assert {"VOICE_COMMAND", "VOICE_APPROVAL", "ACTION_APPROVED", "DRAFT_PREPARED"} <= kinds


def test_voice_no_denies_and_rolls_back(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    denied = _ask(client, case_id, "Tidak.", {"voice": True, "pending_action": prop})
    assert denied["rollback_drafts"] is True
    assert denied["proposed_action"] is None
    assert _ambiguous_units(client, case_id)  # tidak ada yang tersimpan


def test_resume_does_not_approve(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    uid = prop["payload"]["unit_id"]
    continued = _ask(client, case_id, "Lanjut.", {"pending_action": prop})
    assert uid in [u["unit_id"] for u in _ambiguous_units(client, case_id)]
    assert continued["proposed_action"] is not None  # dipresentasikan ulang, bukan di-approve


def test_pause_resume_stop_controls(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    paused = _ask(client, case_id, "Tunggu.")
    assert paused["pause_agent"] is True
    assert paused["guidance_plan"] in (None, [])
    resumed = _ask(client, case_id, "Lanjut.")
    assert resumed["pause_agent"] is False and resumed["stop_agent"] is False
    assert resumed["message"]
    stopped = _ask(client, case_id, "Hentikan AI.")
    assert stopped["stop_agent"] is True
    assert stopped["rollback_drafts"] is True


def test_show_evidence_plan(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Buka buktinya.")
    types = _plan_types(body)
    assert "NAVIGATE_INTERNAL" in types and "OPEN_EVIDENCE" in types
    nav = next(s for s in body["guidance_plan"] if s["type"] == "NAVIGATE_INTERNAL")
    assert nav["route"] == "intake"


def test_red_voice_still_denied(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Isi OTP saya 123456.", {"voice": True})
    assert body["guidance_plan"] in (None, [])
    assert body["proposed_action"] is None
    assert "OTP" in body["message"] or "berhenti" in body["message"]
