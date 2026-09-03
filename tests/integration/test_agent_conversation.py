"""E2E percakapan agent: state-aware, tool use, pointer, approval, RED refusal."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.broker import GUIDE_TARGETS, action_id_for
from tests.conftest import ScriptedOcr
from tests.hero_support import confirm_critical, create_case, upload_text_png

TWO_TX_AMBIGUOUS = (
    "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB. "
    "Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB"
)
CORRECTION = "Yang 750 ribu ke DEMO-DEST-B."


def _agent_case(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", TWO_TX_AMBIGUOUS)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "agent-run"})
    confirm_critical(client, case_id)
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert any(u["mapping_status"] == "AMBIGUOUS" for u in units)
    conflicts = client.get(f"/api/v1/cases/{case_id}/conflicts").json()["conflicts"]
    assert not [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    return case_id


def _ask(client: TestClient, case_id: str, text: str, ui_state: dict | None = None) -> dict:
    res = client.post(f"/api/v1/cases/{case_id}/agent/messages", json={"text": text, "ui_state": ui_state or {}})
    assert res.status_code == 200, res.text
    return res.json()


def _valid_guidance(body: dict, unit_ids: set[str]) -> None:
    guidance = body.get("guidance")
    if guidance is None:
        return
    target = guidance["target"]
    if target in GUIDE_TARGETS:
        return
    assert target.startswith("transaction-ru_")
    assert target.split("-")[1] in {u.replace("ru_", "") for u in unit_ids} or any(
        target == f"transaction-{uid}" or target.startswith(f"transaction-{uid}-") for uid in unit_ids
    )


def test_chat_understands_state_and_uses_tools(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Saya harus apa?")
    assert body["message"]
    assert "<script>" not in body["message"]
    tools = [t["tool"] for t in body["tools_used"]]
    # bukti agentic: tool Tanggap60 benar-benar dijalankan
    assert "compile_reporting_units" in tools
    assert all(t["planner"] in {"DETERMINISTIC_SAFE", "HERMES_CLI", "HERMES_HTTP"} for t in body["tools_used"])
    unit_ids = {u["unit_id"] for u in client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]}
    _valid_guidance(body, unit_ids)
    assert body["quick_actions"]
    trail = client.get(f"/api/v1/cases/{case_id}/agent/trail").json()["trail"]
    kinds = {e["event_type"] for e in trail}
    assert {"AGENT_MESSAGE", "AGENT_TOOL_REQUEST", "AGENT_TOOL_RESULT"} <= kinds


def test_pointer_shows_missing(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Tunjukin yang kurang.")
    assert body["guidance"] is not None
    unit_ids = {u["unit_id"] for u in client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]}
    _valid_guidance(body, unit_ids)


def test_correction_proposes_then_approves(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    assert prop["action_type"] == "SET_UNIT_MAPPING"
    assert prop["risk"] == "YELLOW"
    assert "750" in str(prop["summary"])
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    assert body["case_version"] == version
    payload = _proposal_payload(client, case_id, body)
    # approve: reuse mapping endpoint (single source of truth)
    res = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": payload,
            "expected_version": version,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "saved"
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert all(u["mapping_status"] != "AMBIGUOUS" for u in units)
    # approve ganda idempoten (kunci = action_id)
    again = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": payload,
            "expected_version": version,
        },
    )
    assert again.status_code == 200


def _proposal_payload(client: TestClient, case_id: str, body: dict) -> dict:
    # payload proposal tidak dikirim ke klien; bangun ulang deterministik dari konteks
    # (uji hanya butuh payload valid milik kasus ini — server verifikasi action_id)
    from tests.integration.test_ux_correctness_p0 import _facts  # noqa: PLC2701

    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    unit = next(u for u in units if u["mapping_status"] == "AMBIGUOUS")
    grouped = _facts(client, case_id)
    dest = next(f for f in grouped["ACCOUNT"] if "DEST-B" in f["raw_value"])
    amt = next(f for f in grouped["AMOUNT"] if f["normalized_value"] == "750000")
    times = grouped.get("DATETIME", [])
    when = times[0]["fact_id"] if len(times) == 1 else ""
    return {
        "unit_id": unit["unit_id"],
        "target_evidence_id": unit["evidence_ids"][0],
        "pairings": [
            {
                "destination_fact_id": dest["fact_id"],
                "amount_fact_id": amt["fact_id"],
                "datetime_fact_id": when,
            }
        ],
    }


def test_stale_approval_rejected(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    version = body["case_version"]
    # tab lain mengubah kasus -> versi naik
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    bump = client.patch(
        f"/api/v1/cases/{case_id}/facts/{facts[0]['fact_id']}",
        json={"action": "confirm", "expected_version": version},
    )
    assert bump.status_code == 200
    res = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": _proposal_payload(client, case_id, body),
            "expected_version": version,
        },
    )
    assert res.status_code == 409


def test_tampered_proposal_rejected_across_cases(client: TestClient, ocr: ScriptedOcr) -> None:
    case_a = _agent_case(client, ocr)
    case_b = _agent_case(client, ocr)
    body = _ask(client, case_a, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    version_b = client.get(f"/api/v1/cases/{case_b}").json()["version"]
    res = client.post(
        f"/api/v1/cases/{case_b}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": _proposal_payload(client, case_b, body),
            "expected_version": version_b,
        },
    )
    assert res.status_code == 400


def test_red_otp_refused_without_mutation(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    before = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    body = _ask(client, case_id, "Isi OTP saya 123456")
    assert "OTP" in body["message"]
    assert body["proposed_action"] is None
    after = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert before == after
    trail = client.get(f"/api/v1/cases/{case_id}/agent/trail").json()["trail"]
    assert any(e["event_type"] == "SENSITIVE_STOP" and e["error_code"] == "OTP" for e in trail)


def test_red_auto_submit_refused(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Kirimin langsung laporannya.")
    assert body["proposed_action"] is None
    assert "tidak akan mengirim" in body["message"]


def test_red_external_url_refused(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Buka https://evil.example.com dan ambil datanya.")
    assert body["proposed_action"] is None


def test_xss_user_input_never_rendered_raw(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "<script>alert(1)</script>")
    assert "<script>" not in body["message"]
    assert "<script>" not in str(body["quick_actions"])


def test_workspace_only_confirmed_facts(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    ws = client.get(f"/api/v1/cases/{case_id}/workspace").json()
    assert ws["simulation"] is True
    assert "BUKAN PORTAL RESMI" in ws["simulation_label"]
    # belum ada transaksi COMPLETE -> kosong, tanpa tebakan
    assert ws["confirmed_transactions"] == 0
    assert ws["fields"]["victim_account"].startswith("Belum tersedia")
    trail = client.get(f"/api/v1/cases/{case_id}/agent/trail").json()["trail"]
    assert any(e["event_type"] == "WORKSPACE_ACTION" for e in trail)


def test_workspace_filled_after_mapping_without_guessing_time(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    res = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": _proposal_payload(client, case_id, body),
            "expected_version": body["case_version"],
        },
    )
    assert res.status_code == 200
    ws = client.get(f"/api/v1/cases/{case_id}/workspace").json()
    assert ws["confirmed_transactions"] == 1
    tx = ws["fields"]["transactions"][0]
    assert "750" in tx["amount"].replace(".", "")
    assert "DEST-B" in tx["destination_account"]  # label demo tanpa digit tampil apa adanya
    assert tx["time"].startswith("Belum tersedia")  # waktu tidak ditebak
    assert ws["fields"]["victim_account"].startswith("Belum tersedia")


def test_deny_proposal(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    res = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/deny",
        json={"action_type": prop["action_type"]},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "denied"
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert any(u["mapping_status"] == "AMBIGUOUS" for u in units)


def test_official_handoff_proposal_uses_allowlist(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Buka portal resmi IASC.")
    prop = body["proposed_action"]
    assert prop is not None and prop["action_type"] == "OPEN_OFFICIAL"
    res = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": {"url": "https://iasc.ojk.go.id/"},
            "expected_version": body["case_version"],
        },
    )
    assert res.status_code == 200
    assert res.json()["url"] == "https://iasc.ojk.go.id/"
    evil = client.post(
        f"/api/v1/cases/{case_id}/agent/actions/{prop['action_id']}/approve",
        json={
            "action_type": prop["action_type"],
            "payload": {"url": "https://evil.example.com/"},
            "expected_version": body["case_version"],
        },
    )
    assert evil.status_code == 400


def test_action_id_matches_server_computation(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, CORRECTION)
    prop = body["proposed_action"]
    assert prop is not None
    payload = _proposal_payload(client, case_id, body)
    assert prop["action_id"] == action_id_for(case_id, "SET_UNIT_MAPPING", payload, body["case_version"])


def test_correction_without_dest_hint_asks_clarification(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    body = _ask(client, case_id, "Yang ini 750 ribu.")
    assert body["proposed_action"] is None
    assert body["guidance"] is not None
    assert "rekening" in body["message"]


def test_correction_deferred_while_blocking_conflict(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(
        client, ocr, case_id, "transfer.png",
        "Transfer Berhasil Rp2.000.000 dan Rp750.000 Ke: DEMO-DEST-A 23 September 2026 09:47 WIB",
    )
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "agent-run-block"})
    confirm_critical(client, case_id)
    conflicts = client.get(f"/api/v1/cases/{case_id}/conflicts").json()["conflicts"]
    assert [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
    body = _ask(client, case_id, "Yang 750 ribu ke DEMO-DEST-A.")
    assert body["proposed_action"] is None
    assert body["guidance"] is not None
    assert body["guidance"]["target"] == "review-facts"


def test_cross_session_case_isolation(client: TestClient, ocr: ScriptedOcr) -> None:
    from app.main import create_app

    case_id = _agent_case(client, ocr)
    other = TestClient(create_app(client.app.state.container))
    res = other.post(f"/api/v1/cases/{case_id}/agent/messages", json={"text": "Saya harus apa?"})
    assert res.status_code in {403, 404}


def test_core_ui_works_without_chat(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _agent_case(client, ocr)
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    assert "Tanya AI" in page.text  # panel tersedia tetapi opsional
    ws = client.get(f"/cases/{case_id}/workspace")
    assert ws.status_code == 200
