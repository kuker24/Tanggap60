from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import (
    CHAT,
    TRANSFER,
    approve_to_handoff,
    create_case,
    resolve_amount_conflict,
    tool_names,
    upload_text_png,
)

POST_ZIP = {
    "action_plan.pdf",
    "evidence_pack.pdf",
    "readiness_report.pdf",
    "bank_handoff_pack.pdf",
    "iasc_handoff_pack.pdf",
    "police_handoff_pack.pdf",
    "case.json",
    "handoff.md",
    "manifest.sha256",
}


def test_readiness_rejects_cross_session(client: TestClient) -> None:
    case_id = create_case(client)
    other = TestClient(client.app)
    res = other.get(f"/api/v1/cases/{case_id}/readiness")
    assert res.status_code == 403


def test_hero_readiness_improves_then_packs(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "chat.png", CHAT)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "preflight-1"})
    assert run.status_code == 202
    blocked = client.get(f"/api/v1/cases/{case_id}/readiness").json()
    assert blocked["official_status"] == "NOT_VERIFIED"
    assert blocked["overall_status"] == "BLOCKED"
    page = client.get(f"/cases/{case_id}/readiness")
    assert page.status_code == 200
    assert "Bank/PJP" in page.text
    assert "IASC" in page.text
    assert "Kepolisian" in page.text
    resolve_amount_conflict(client, case_id)
    after = client.get(f"/api/v1/cases/{case_id}/readiness").json()
    assert after["overall_status"] != "BLOCKED"
    result = approve_to_handoff(client, case_id)
    assert result["state"] == "HANDOFF_READY"
    tools = tool_names(client, case_id)
    assert "assess_handoff_readiness" in tools
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    types = {a["type"] for a in arts}
    assert {"READINESS_REPORT", "BANK_HANDOFF_PACK", "IASC_HANDOFF_PACK", "POLICE_HANDOFF_PACK"}.issubset(types)
    assert all(a["verify_status"] == "PASS" for a in arts)
    zip_art = next(a for a in arts if a["type"] == "CASE_ZIP")
    blob = client.get(f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download")
    assert blob.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(blob.content)).namelist())
    assert names == POST_ZIP
    case_json = next(a for a in arts if a["type"] == "CASE_JSON")
    raw = client.get(f"/api/v1/cases/{case_id}/artifacts/{case_json['artifact_id']}/download")
    payload = json.loads(raw.content.decode())
    assert payload["schema_version"] == "2.1"
    assert payload["official_status"] == "NOT_VERIFIED"
    assert payload["readiness"]["official_status"] == "NOT_VERIFIED"
    stale = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "old-snap"},
        json={"snapshot_hash": "0" * 64, "accepted_notice": True},
    )
    assert stale.status_code == 409


def test_cekdulu_not_forced_into_post_packs(client: TestClient) -> None:
    case_id = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"}).json()["case_id"]
    client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"url": "http://login.ojk-secure.example:8080/pay"})
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "cek-pre"})
    ready = client.get(f"/api/v1/cases/{case_id}/readiness").json()
    assert ready["overall_status"] == "BLOCKED"
    assert "assess_handoff_readiness" not in [
        e.get("tool_name") for e in client.get(f"/api/v1/cases/{case_id}/events").json()["events"]
    ]
