from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.policies import contains_absolute_copy


def test_cekdulu_url_indicators_and_decision(client: TestClient) -> None:
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"})
    assert created.status_code == 201
    case_id = created.json()["case_id"]
    assert created.json()["route"] == "PRE_INCIDENT_CHECK"
    added = client.post(
        f"/api/v1/cases/{case_id}/evidence/text",
        json={"url": "http://login.ojk-secure.example:8080/pay"},
    )
    assert added.status_code == 202
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "cek-1"})
    assert run.status_code == 202
    assert "purge_case" not in run.json().get("trace", [])
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        if fact["review_status"] == "CANDIDATE":
            client.patch(
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    body = draft.json()
    blob = str(body).lower()
    assert not contains_absolute_copy(blob)
    assert "pasti aman" not in blob
    assert "pasti scam" not in blob
    tools = [
        e.get("tool_name")
        for e in client.get(f"/api/v1/cases/{case_id}/events").json()["events"]
        if e.get("tool_name")
    ]
    assert "build_preincident_brief" in tools
    decision = client.post(
        f"/api/v1/cases/{case_id}/decision",
        json={"decision": "VERIFY_VIA_OFFICIAL_CHANNEL"},
    )
    assert decision.status_code == 200
    assert decision.json()["actor"] == "USER"


def test_cekdulu_never_fetches_localhost(client: TestClient) -> None:
    case_id = client.post(
        "/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"}
    ).json()["case_id"]
    client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"url": "http://127.0.0.1/secret"})
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "cek-local"})
    assert run.status_code == 202
    assert run.json().get("status") != "FAILED_SAFE"
