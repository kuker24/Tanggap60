from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "demo_tanggap60"
REQUIRED = (
    "inspect_evidence",
    "extract_candidate_facts",
    "validate_case_facts",
    "build_postincident_plan",
    "compile_artifacts",
    "verify_artifacts",
    "prepare_official_handoff",
    "record_handoff_receipt",
)


def _keep_session(client: httpx.Client, response: httpx.Response) -> None:
    header = response.headers.get("set-cookie") or ""
    if "t60_sid=" not in header:
        return
    value = header.split("t60_sid=", 1)[1].split(";", 1)[0]
    client.cookies.set("t60_sid", value)


def _call(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    response = client.request(method, url, **kwargs)
    _keep_session(client, response)
    return response


def _wait_state(client: httpx.Client, case_id: str, wanted: set[str], timeout: float) -> str:
    deadline = time.time() + timeout
    state = ""
    while time.time() < deadline:
        status = _call(client, "GET", f"/api/v1/cases/{case_id}")
        status.raise_for_status()
        state = str(status.json().get("state") or "")
        if state in wanted or state == "FAILED_SAFE":
            return state
        time.sleep(0.5)
    return state


def _tools(client: httpx.Client, case_id: str) -> list[str]:
    events = _call(client, "GET", f"/api/v1/cases/{case_id}/events")
    if events.status_code != 200:
        return []
    return [e.get("tool_name") for e in events.json().get("events", []) if e.get("tool_name")]


def _evidence_bytes() -> tuple[bytes, bytes]:
    if (FIX / "01_chat.png").exists():
        return (FIX / "01_chat.png").read_bytes(), (FIX / "03_transfer.png").read_bytes()
    sys.path.insert(0, str(ROOT))
    from tests.fixture_render import CHAT, TRANSFER, png_bytes

    return png_bytes(CHAT), png_bytes(TRANSFER)


def _resolve_conflicts(client: httpx.Client, case_id: str) -> None:
    conflicts = _call(client, "GET", f"/api/v1/cases/{case_id}/conflicts").json().get("conflicts", [])
    blocking = [c for c in conflicts if c.get("severity") == "BLOCKING" and c.get("status") == "OPEN"]
    if not blocking:
        return
    facts = {f["fact_id"]: f for f in _call(client, "GET", f"/api/v1/cases/{case_id}/facts").json().get("facts", [])}
    fact_ids = blocking[0]["fact_ids"]
    winner = next(
        (
            fid
            for fid in fact_ids
            if "2.750" in str(facts.get(fid, {}).get("raw_value"))
            or facts.get(fid, {}).get("normalized_value") == "2750000"
        ),
        fact_ids[0],
    )
    version = _call(client, "GET", f"/api/v1/cases/{case_id}").json()["version"]
    res = _call(
        client,
        "POST",
        f"/api/v1/cases/{case_id}/conflicts/{blocking[0]['conflict_id']}/resolve",
        json={"resolution_fact_id": winner, "expected_version": version},
    )
    res.raise_for_status()


def _confirm_critical(client: httpx.Client, case_id: str) -> None:
    version = _call(client, "GET", f"/api/v1/cases/{case_id}").json()["version"]
    for fact in _call(client, "GET", f"/api/v1/cases/{case_id}/facts").json().get("facts", []):
        if fact.get("criticality") == "CRITICAL" and fact.get("review_status") == "CANDIDATE":
            patched = _call(
                client,
                "PATCH",
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            patched.raise_for_status()
            version = _call(client, "GET", f"/api/v1/cases/{case_id}").json()["version"]


def run_hero(base: str, wait: float = 120.0) -> dict[str, Any]:
    started = time.perf_counter()
    client = httpx.Client(base_url=base.rstrip("/"), timeout=180.0, follow_redirects=True)
    live = _call(client, "GET", "/health/live")
    live.raise_for_status()
    created = _call(client, "POST", "/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    created.raise_for_status()
    case_id = created.json()["case_id"]
    chat, transfer = _evidence_bytes()
    up = _call(
        client,
        "POST",
        f"/api/v1/cases/{case_id}/evidence",
        files=[
            ("files", ("chat.png", chat, "image/png")),
            ("files", ("transfer.png", transfer, "image/png")),
        ],
    )
    up.raise_for_status()
    run = _call(
        client,
        "POST",
        f"/api/v1/cases/{case_id}/runs",
        headers={"Idempotency-Key": f"smoke-{uuid.uuid4().hex[:8]}"},
    )
    run.raise_for_status()
    state = _wait_state(client, case_id, {"REVIEW_REQUIRED", "READY_FOR_ACTION"}, wait)
    if state not in {"REVIEW_REQUIRED", "READY_FOR_ACTION"}:
        raise SystemExit(f"unexpected state after ingest {state} tools={_tools(client, case_id)}")
    _resolve_conflicts(client, case_id)
    _confirm_critical(client, case_id)
    draft = _call(client, "POST", f"/api/v1/cases/{case_id}/draft")
    draft.raise_for_status()
    snap = draft.json()["snapshot_hash"]
    state = str(draft.json().get("state") or _call(client, "GET", f"/api/v1/cases/{case_id}").json()["state"])
    if state not in {"WAITING_APPROVAL", "READY_FOR_ACTION", "GENERATING", "HANDOFF_READY"}:
        facts = _call(client, "GET", f"/api/v1/cases/{case_id}/facts").json()
        raise SystemExit(f"draft did not unlock approval state={state} facts={facts}")
    approval = _call(
        client,
        "POST",
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": f"appr-{uuid.uuid4().hex[:8]}"},
        json={"snapshot_hash": snap, "accepted_notice": True},
    )
    if approval.status_code >= 400:
        raise SystemExit(f"approval failed {approval.status_code} {approval.text[:300]}")
    state = str(approval.json().get("state") or "")
    if state != "HANDOFF_READY":
        follow = _call(
            client,
            "POST",
            f"/api/v1/cases/{case_id}/runs",
            headers={"Idempotency-Key": f"pack-{uuid.uuid4().hex[:8]}"},
        )
        follow.raise_for_status()
        state = _wait_state(client, case_id, {"HANDOFF_READY"}, wait)
    if state != "HANDOFF_READY":
        raise SystemExit(f"unexpected state after approval {state} tools={_tools(client, case_id)}")
    arts = _call(client, "GET", f"/api/v1/cases/{case_id}/artifacts").json().get("artifacts", [])
    if not arts or any(a.get("verify_status") != "PASS" for a in arts):
        verify = _call(client, "POST", f"/api/v1/cases/{case_id}/artifacts/verify")
        verify.raise_for_status()
        arts = _call(client, "GET", f"/api/v1/cases/{case_id}/artifacts").json().get("artifacts", [])
    if not arts or any(a.get("verify_status") != "PASS" for a in arts):
        raise SystemExit(f"artifacts not PASS {arts}")
    handoff = _call(client, "GET", f"/api/v1/cases/{case_id}/handoff")
    handoff.raise_for_status()
    if "iasc.ojk.go.id" not in str(handoff.json().get("official_url", "")):
        raise SystemExit("handoff URL missing")
    receipt = _call(
        client,
        "POST",
        f"/api/v1/cases/{case_id}/receipt",
        headers={"Idempotency-Key": f"rcpt-{uuid.uuid4().hex[:8]}"},
        json={"ticket_text": "IASC123456", "ocr_text": "IASC123456"},
    )
    receipt.raise_for_status()
    if receipt.json().get("official_status") != "NOT_VERIFIED":
        raise SystemExit("official_status must stay NOT_VERIFIED")
    tools = _tools(client, case_id)
    mode = _call(client, "GET", "/api/v1/agent/tools").json().get("hermes_mode")
    missing = [name for name in REQUIRED if name not in tools]
    elapsed = time.perf_counter() - started
    result = {
        "case_id": case_id,
        "state": state,
        "tools": tools,
        "missing": missing,
        "hermes_mode": mode,
        "elapsed_s": round(elapsed, 3),
        "artifacts": len(arts),
        "receipt": receipt.json().get("local_match_status"),
    }
    if missing:
        raise SystemExit(f"missing tools {missing} result={result}")
    return result


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    result = run_hero(base)
    print(
        "HERO_SMOKE_PASS "
        f"state={result['state']} tools={len(result['tools'])} "
        f"hermes={result['hermes_mode']} elapsed_s={result['elapsed_s']} "
        f"trace={result['tools']}"
    )


if __name__ == "__main__":
    main()
