from __future__ import annotations

import io
import sys
import time
import uuid
import zipfile
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
    "assess_handoff_readiness",
    "compile_artifacts",
    "verify_artifacts",
    "prepare_official_handoff",
    "record_handoff_receipt",
)
REQUIRED_RESCUE = (
    "inspect_evidence",
    "extract_candidate_facts",
    "validate_case_facts",
    "build_postincident_plan",
    "compile_reporting_units",
    "assess_handoff_readiness",
    "recommend_next_action",
    "compile_artifacts",
    "verify_artifacts",
    "prepare_official_handoff",
    "record_handoff_receipt",
)
REASONING = {
    "inspect_evidence",
    "extract_candidate_facts",
    "validate_case_facts",
    "build_postincident_plan",
    "assess_handoff_readiness",
}
MECHANICAL = {
    "compile_reporting_units",
    "recommend_next_action",
    "compile_artifacts",
    "verify_artifacts",
    "prepare_official_handoff",
}
POST_ZIP_NAMES = {
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
NEW_ARTIFACTS = {
    "READINESS_REPORT",
    "BANK_HANDOFF_PACK",
    "IASC_HANDOFF_PACK",
    "POLICE_HANDOFF_PACK",
}


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


def _trace(client: httpx.Client, case_id: str) -> dict[str, Any]:
    res = _call(client, "GET", f"/api/v1/cases/{case_id}/trace")
    if res.status_code != 200:
        return {"steps": [], "hermes_cli_used": False}
    return res.json()


def _evidence_bytes() -> tuple[bytes, bytes]:
    if (FIX / "01_chat.png").exists():
        return (FIX / "01_chat.png").read_bytes(), (FIX / "03_transfer.png").read_bytes()
    sys.path.insert(0, str(ROOT))
    from tests.fixture_render import CHAT, TRANSFER, png_bytes

    return png_bytes(CHAT), png_bytes(TRANSFER)


def _read_fixture(name: str) -> bytes:
    p = FIX / name
    if p.exists():
        return p.read_bytes()
    raise FileNotFoundError(name)


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


def _confirm_facts_for_evidence(client: httpx.Client, case_id: str, target_evid: str | None = None, only_types: set[str] | None = None) -> None:
    version = _call(client, "GET", f"/api/v1/cases/{case_id}").json()["version"]
    facts = _call(client, "GET", f"/api/v1/cases/{case_id}/facts").json().get("facts", [])
    for fact in facts:
        if fact.get("review_status") != "CANDIDATE":
            continue
        if target_evid and fact.get("source_evidence_id") != target_evid:
            continue
        if only_types and fact.get("type") not in only_types:
            continue
        # confirm
        patched = _call(
            client,
            "PATCH",
            f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
            json={"action": "confirm", "expected_version": version},
        )
        patched.raise_for_status()
        version = _call(client, "GET", f"/api/v1/cases/{case_id}").json()["version"]


def _hermes_configured(client: httpx.Client) -> bool:
    try:
        agent = _call(client, "GET", "/api/v1/agent/tools").json()
        return bool(agent.get("hermes_bin_configured") or agent.get("hermes_cli_configured") or agent.get("hermes_mode") == "cli" or agent.get("hermes_cli_used"))
    except Exception:
        return False


def run_hero(base: str, wait: float = 120.0, scenario: str = "legacy") -> dict[str, Any]:
    if scenario == "rescue_multi":
        return run_rescue_hero(base, wait)
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
        files=[("files", ("transfer.png", transfer, "image/png"))],
    )
    up.raise_for_status()
    up2 = _call(client, "POST", f"/api/v1/cases/{case_id}/evidence/text", json={"text": "Kirim dulu Rp2.500.000 ke rekening ini ya biar pesanan diproses"})
    if up2.status_code >= 400:
        up = _call(
            client,
            "POST",
            f"/api/v1/cases/{case_id}/evidence",
            files=[("files", ("chat.png", chat, "image/png"))],
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
    types = {str(a.get("type")) for a in arts}
    if not NEW_ARTIFACTS.issubset(types):
        raise SystemExit(f"missing preflight artifacts {NEW_ARTIFACTS - types}")
    zip_art = next(a for a in arts if a.get("type") == "CASE_ZIP")
    packed = _call(client, "GET", f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download")
    packed.raise_for_status()
    names = set(zipfile.ZipFile(io.BytesIO(packed.content)).namelist())
    if names != POST_ZIP_NAMES:
        raise SystemExit(f"zip contents {sorted(names)}")
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
    if receipt.json().get("local_match_status") != "MATCH":
        raise SystemExit(f"receipt not MATCH {receipt.json()}")
    state = _wait_state(client, case_id, {"RECEIPT_RECORDED"}, 15)
    if state != "RECEIPT_RECORDED":
        raise SystemExit(f"expected RECEIPT_RECORDED got {state}")
    tools = _tools(client, case_id)
    trace = _trace(client, case_id)
    agent = _call(client, "GET", "/api/v1/agent/tools").json()
    missing = [name for name in REQUIRED if name not in tools]
    elapsed = time.perf_counter() - started
    cli_used = bool(trace.get("hermes_cli_used"))
    result = {
        "case_id": case_id,
        "state": state,
        "tools": tools,
        "missing": missing,
        "hermes_mode": agent.get("hermes_mode"),
        "hermes_cli_used": cli_used,
        "elapsed_s": round(elapsed, 3),
        "artifacts": len(arts),
        "receipt": receipt.json().get("local_match_status"),
        "verification": "PASS",
        "official_status": "NOT_VERIFIED",
        "trace_steps": trace.get("steps") or [],
        "metrics": _call(client, "GET", "/demo/metrics").json(),
    }
    if missing:
        raise SystemExit(f"missing tools {missing} result={result}")
    if state == "REVIEW_REQUIRED":
        raise SystemExit("REVIEW_REQUIRED is not a final hero success")
    return result


def run_rescue_hero(base: str, wait: float = 120.0) -> dict[str, Any]:
    started = time.perf_counter()
    client = httpx.Client(base_url=base.rstrip("/"), timeout=180.0, follow_redirects=True)
    live = _call(client, "GET", "/health/live")
    live.raise_for_status()
    # create incident
    created = _call(client, "POST", "/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    created.raise_for_status()
    case_id = created.json()["case_id"]
    # upload 3 images: transfer A (2M complete), transfer B (750k complete but will be held), chat image
    # Use fixtures 04_transfer_a.png, 06_transfer_b_complete.png, 01_chat.png — all as image uploads to keep OCR cost real
    try:
        a_bytes = _read_fixture("04_transfer_a.png")
        b_bytes = _read_fixture("06_transfer_b_complete.png")
        chat_bytes = _read_fixture("01_chat.png")
    except FileNotFoundError:
        sys.path.insert(0, str(ROOT))
        from tests.fixture_render import CHAT, png_bytes
        a_bytes = png_bytes("Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB Dari: DEMO-VICTIM-MASKED")
        b_bytes = png_bytes("Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB Dari: DEMO-VICTIM-MASKED")
        chat_bytes = png_bytes(CHAT)
    # upload each separately to capture evidence ids
    up_a = _call(client, "POST", f"/api/v1/cases/{case_id}/evidence", files=[("files", ("transfer_a.png", a_bytes, "image/png"))])
    up_a.raise_for_status()
    evid_a = (up_a.json().get("evidence") or [{}])[0].get("evidence_id")
    up_b = _call(client, "POST", f"/api/v1/cases/{case_id}/evidence", files=[("files", ("transfer_b.png", b_bytes, "image/png"))])
    up_b.raise_for_status()
    evid_b = (up_b.json().get("evidence") or [{}])[0].get("evidence_id")
    up_chat = _call(client, "POST", f"/api/v1/cases/{case_id}/evidence", files=[("files", ("chat.png", chat_bytes, "image/png"))])
    up_chat.raise_for_status()
    # fallback if evidence list empty (single upload returns list)
    if not evid_a or not evid_b:
        evid_list = _call(client, "GET", f"/api/v1/cases/{case_id}/evidence").json().get("evidence", [])
        # map by original name
        for e in evid_list:
            name = str(e.get("original_name_display") or e.get("filename") or "")
            if "transfer_a" in name:
                evid_a = e["evidence_id"]
            elif "transfer_b" in name:
                evid_b = e["evidence_id"]
        # if still not, take first two as A,B
        if not evid_a or not evid_b:
            ids = [e["evidence_id"] for e in evid_list]
            if len(ids) >= 2:
                evid_a, evid_b = ids[0], ids[1]
    # ingest
    run = _call(client, "POST", f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": f"smoke-{uuid.uuid4().hex[:8]}"})
    run.raise_for_status()
    state = _wait_state(client, case_id, {"REVIEW_REQUIRED", "READY_FOR_ACTION"}, wait)
    if state not in {"REVIEW_REQUIRED", "READY_FOR_ACTION"}:
        raise SystemExit(f"unexpected state after ingest {state} tools={_tools(client, case_id)}")
    _resolve_conflicts(client, case_id)
    # Human fact review: confirm only Unit A facts, leave Unit B time as CANDIDATE to make it INCOMPLETE initially
    # Confirm all facts for A, plus chat claim? Chat not critical, but we confirm A only
    if evid_a:
        _confirm_facts_for_evidence(client, case_id, evid_a)
    # Also confirm chat? Not needed for READY check but we keep shared evidence available via image already
    # Do not confirm B yet — leave its time/dest/amount as CANDIDATE for now to create INCOMPLETE
    # Call draft to trigger compile_reporting_units, assess, recommend
    draft = _call(client, "POST", f"/api/v1/cases/{case_id}/draft")
    draft.raise_for_status()
    # ensure orchestrator progressed
    state = _wait_state(client, case_id, {"WAITING_APPROVAL", "READY_FOR_ACTION", "GENERATING", "HANDOFF_READY"}, wait)
    # Check reporting units
    ru_res = _call(client, "GET", f"/api/v1/cases/{case_id}/reporting-units")
    ru_res.raise_for_status()
    ru_data = ru_res.json()
    units = ru_data.get("reporting_units") or []
    if len(units) != 2:
        raise SystemExit(f"reporting_units expected 2 got {len(units)} {units}")
    # identify A and B by destination
    unit_a = next((u for u in units if "DEMO-DEST-A" in str(u.get("destination_account") or "")), None)
    unit_b = next((u for u in units if "DEMO-DEST-B" in str(u.get("destination_account") or "")), None)
    if not unit_a or not unit_b:
        # fallback by sorting
        units_sorted = sorted(units, key=lambda x: x.get("unit_id"))
        unit_a, unit_b = units_sorted[0], units_sorted[1]
    if unit_a.get("mapping_status") != "COMPLETE":
        raise SystemExit(f"Unit A expected COMPLETE got {unit_a}")
    # Unit B should start not READY (INCOMPLETE due to missing reviewed time)
    if unit_b.get("mapping_status") == "COMPLETE":
        # check readiness, maybe still not READY due to not confirmed
        readiness = ru_data.get("readiness") or {}
        rb = readiness.get("readiness_by_unit") or {}
        b_ready = rb.get(unit_b.get("unit_id"), {})
        if b_ready.get("BANK_PJP") == "READY":
            # It is ready too early, we need to make it incomplete by leaving time unconfirmed — but currently 06 has time, so it became COMPLETE.
            # Force incomplete by treating as not yet confirmed: we already left B unconfirmed, but compiler still used CANDIDATE? Actually compiler uses only CONFIRMED, so if B's time is CANDIDATE, it should be INCOMPLETE.
            # If it is COMPLETE, means we accidentally confirmed B. So we need to ensure B not confirmed.
            raise SystemExit(f"Unit B should start not READY before human correction, got COMPLETE and ready {b_ready} — check confirm logic")
    # Check next_best_action
    nxt = _call(client, "GET", f"/api/v1/cases/{case_id}/next-action").json()
    target = nxt.get("target_unit_id")
    code = nxt.get("code")
    if target != unit_a.get("unit_id"):
        raise SystemExit(f"next_best_action target expected Unit A {unit_a.get('unit_id')} got {nxt}")
    if code not in ("CONTACT_BANK_PJP", "PREPARE_IASC_UNIT"):
        raise SystemExit(f"next_best_action expected CONTACT_BANK_PJP or PREPARE_IASC_UNIT got {code} {nxt}")
    # LAKUKAN SEKARANG act on Unit A — human then fixes Unit B by confirming its remaining facts
    if evid_b:
        _confirm_facts_for_evidence(client, case_id, evid_b)
    else:
        _confirm_critical(client, case_id)
    # re-trigger orchestration for updated readiness
    _call(client, "POST", f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": f"fix-{uuid.uuid4().hex[:8]}"})
    # ignore 202 queued
    time.sleep(0.5)
    draft2 = _call(client, "POST", f"/api/v1/cases/{case_id}/draft")
    draft2.raise_for_status()
    snap = draft2.json()["snapshot_hash"]
    state = str(draft2.json().get("state") or _call(client, "GET", f"/api/v1/cases/{case_id}").json()["state"])
    # Re-check units after correction
    ru_res2 = _call(client, "GET", f"/api/v1/cases/{case_id}/reporting-units")
    ru_res2.raise_for_status()
    ru_data2 = ru_res2.json()
    units2 = ru_data2.get("reporting_units") or []
    if len(units2) != 2:
        raise SystemExit(f"after fix reporting_units expected 2 got {len(units2)}")
    for u in units2:
        if u.get("mapping_status") != "COMPLETE":
            raise SystemExit(f"after fix all units should be COMPLETE got {u}")
    readiness2 = ru_data2.get("readiness") or {}
    rb2 = readiness2.get("readiness_by_unit") or {}
    for u in units2:
        r = rb2.get(u.get("unit_id"), {})
        if r.get("BANK_PJP") != "READY" and r.get("IASC") != "READY":
            raise SystemExit(f"after fix unit {u.get('unit_id')} should be READY got {r}")
    # both units ready -> check next action is still deterministic (bank for earliest)
    nxt2 = _call(client, "GET", f"/api/v1/cases/{case_id}/next-action").json()
    # Should still be CONTACT_BANK for earliest unit
    if nxt2.get("target_unit_id") not in [u.get("unit_id") for u in units2]:
        raise SystemExit(f"after fix next action target invalid {nxt2}")
    # approval
    if not snap:
        raise SystemExit("missing snapshot hash after fix")
    approval = _call(client, "POST", f"/api/v1/cases/{case_id}/approval", headers={"Idempotency-Key": f"appr-{uuid.uuid4().hex[:8]}"}, json={"snapshot_hash": snap, "accepted_notice": True})
    if approval.status_code >= 400:
        raise SystemExit(f"approval failed {approval.status_code} {approval.text[:500]}")
    state = str(approval.json().get("state") or "")
    if state != "HANDOFF_READY":
        follow = _call(client, "POST", f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": f"pack-{uuid.uuid4().hex[:8]}"})
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
    # dynamic 2.2 artifact layout check
    # Expected: 4 base pdfs (action_plan, evidence_pack, readiness_report, police) + case.json + handoff.md + manifest + zip = 8 plus 3 per unit (unit.json, bank, iasc) = 6 => total 14 for 2 units
    zip_art = next((a for a in arts if a.get("type") == "CASE_ZIP"), None)
    if not zip_art:
        raise SystemExit("missing CASE_ZIP")
    packed = _call(client, "GET", f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download")
    packed.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(packed.content))
    names = set(z.namelist())
    expected_base = {"action_plan.pdf", "evidence_pack.pdf", "readiness_report.pdf", "police_handoff_pack.pdf", "case.json", "handoff.md", "manifest.sha256"}
    if not expected_base.issubset(names):
        raise SystemExit(f"zip missing base {expected_base - names} got {sorted(names)}")
    # check per-unit files
    for u in units2:
        uid = u.get("unit_id")
        for suffix in [f"units/{uid}/unit.json", f"units/{uid}/bank_handoff_pack.pdf", f"units/{uid}/iasc_handoff_pack.pdf"]:
            if suffix not in names:
                raise SystemExit(f"zip missing {suffix} got {sorted(names)}")
    # manifest matches
    manifest = z.read("manifest.sha256").decode()
    for line in manifest.strip().splitlines():
        if "  " not in line:
            raise SystemExit(f"manifest line invalid {line}")
        sha, name = line.split("  ", 1)
        data = z.read(name)
        import hashlib
        if hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit(f"manifest hash mismatch for {name}")
    # unit IDs match case.json
    case_json = zipfile.ZipFile(io.BytesIO(packed.content)).read("case.json")
    import json
    case_data = json.loads(case_json)
    if case_data.get("schema_version") != "2.2":
        raise SystemExit(f"case.json schema_version expected 2.2 got {case_data.get('schema_version')}")
    if len(case_data.get("reporting_units") or []) != 2:
        raise SystemExit(f"case.json reporting_units expected 2 got {case_data.get('reporting_units')}")
    if case_data.get("official_status") != "NOT_VERIFIED":
        raise SystemExit("case.json official_status must be NOT_VERIFIED")
    # check trace
    handoff = _call(client, "GET", f"/api/v1/cases/{case_id}/handoff")
    handoff.raise_for_status()
    if "iasc.ojk.go.id" not in str(handoff.json().get("official_url", "")):
        raise SystemExit("handoff URL missing")
    receipt = _call(client, "POST", f"/api/v1/cases/{case_id}/receipt", headers={"Idempotency-Key": f"rcpt-{uuid.uuid4().hex[:8]}"}, json={"ticket_text": "IASC123456", "ocr_text": "IASC123456"})
    receipt.raise_for_status()
    if receipt.json().get("official_status") != "NOT_VERIFIED":
        raise SystemExit("official_status must stay NOT_VERIFIED")
    if receipt.json().get("local_match_status") != "MATCH":
        raise SystemExit(f"receipt not MATCH {receipt.json()}")
    state = _wait_state(client, case_id, {"RECEIPT_RECORDED"}, 15)
    if state != "RECEIPT_RECORDED":
        raise SystemExit(f"expected RECEIPT_RECORDED got {state}")
    tools = _tools(client, case_id)
    trace = _trace(client, case_id)
    agent = _call(client, "GET", "/api/v1/agent/tools").json()
    missing = [name for name in REQUIRED_RESCUE if name not in tools]
    elapsed = time.perf_counter() - started
    # strict hermes check
    hermes_bin_configured = bool(agent.get("hermes_bin_configured") or agent.get("hermes_cli_configured"))
    # if hermes configured, reasoning tools must be HERMES_CLI
    steps = trace.get("steps") or []
    cli_used = bool(trace.get("hermes_cli_used"))
    # fallback detection via planner — strict when hermes configured
    reasoning_fallback = [s for s in steps if s.get("tool_name") in REASONING and s.get("planner") != "HERMES_CLI"]
    # mechanical should be DETERMINISTIC_SAFE, user is USER
    # For strict check, fail if any reasoning planner != HERMES_CLI when hermes configured
    if hermes_bin_configured and reasoning_fallback:
        raise SystemExit(f"strict Hermes failed: reasoning tools not HERMES_CLI {reasoning_fallback} trace={steps}")
    # After fix, hermes_cli_used must be true when hermes configured (at least one success)
    if hermes_bin_configured and not cli_used:
        raise SystemExit(f"strict Hermes failed: hermes_cli_used false but configured {agent} trace={steps}")
    result = {
        "case_id": case_id,
        "state": state,
        "tools": tools,
        "missing": missing,
        "hermes_mode": agent.get("hermes_mode"),
        "hermes_cli_used": cli_used,
        "hermes_cli_configured": hermes_bin_configured,
        "hermes_reasoning_fallback": len(reasoning_fallback),
        "elapsed_s": round(elapsed, 3),
        "artifacts": len(arts),
        "receipt": receipt.json().get("local_match_status"),
        "verification": "PASS",
        "official_status": "NOT_VERIFIED",
        "trace_steps": steps,
        "metrics": _call(client, "GET", "/demo/metrics").json(),
        "reporting_units": units2,
        "next_action": nxt2,
    }
    if missing:
        raise SystemExit(f"missing tools {missing} result={result}")
    return result


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    # default to rescue_multi for competition
    scenario = "rescue_multi"
    if len(sys.argv) > 2:
        scenario = sys.argv[2]
    if scenario == "legacy":
        result = run_hero(base, scenario="legacy")
    else:
        result = run_rescue_hero(base)
    print("HERO_SMOKE_PASS")
    print(f"state={result['state']}")
    print(f"hermes_cli_used={str(result['hermes_cli_used']).lower()}")
    print(f"hermes_cli_configured={str(result.get('hermes_cli_configured', False)).lower()}")
    print(f"hermes_reasoning_fallback={result.get('hermes_reasoning_fallback', 0)}")
    print(f"artifacts={result['artifacts']}")
    print("verification=PASS")
    print(f"receipt={result['receipt']}")
    print(f"elapsed_s={result['elapsed_s']}")
    print(f"trace={result['tools']}")
    print(f"reporting_units={len(result.get('reporting_units', [])) if 'reporting_units' in result else 'legacy'}")


if __name__ == "__main__":
    main()
