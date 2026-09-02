from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "demo_tanggap60"


def _keep_session(client: httpx.Client, response: httpx.Response) -> None:
    header = response.headers.get("set-cookie") or ""
    if "t60_sid=" not in header:
        return
    value = header.split("t60_sid=", 1)[1].split(";", 1)[0]
    client.cookies.set("t60_sid", value)


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    client = httpx.Client(base_url=base, timeout=45.0, follow_redirects=True)
    live = client.get("/health/live")
    live.raise_for_status()
    _keep_session(client, live)
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    created.raise_for_status()
    _keep_session(client, created)
    case_id = created.json()["case_id"]
    if (FIX / "01_chat.png").exists():
        chat = (FIX / "01_chat.png").read_bytes()
        transfer = (FIX / "03_transfer.png").read_bytes()
    else:
        sys.path.insert(0, str(ROOT))
        from tests.fixture_render import CHAT, TRANSFER, png_bytes

        chat = png_bytes(CHAT)
        transfer = png_bytes(TRANSFER)
    up = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[
            ("files", ("chat.png", chat, "image/png")),
            ("files", ("transfer.png", transfer, "image/png")),
        ],
    )
    up.raise_for_status()
    _keep_session(client, up)
    run = client.post(
        f"/api/v1/cases/{case_id}/runs",
        headers={"Idempotency-Key": f"smoke-{uuid.uuid4().hex[:8]}"},
    )
    run.raise_for_status()
    _keep_session(client, run)
    state = run.json().get("state")
    tools = run.json().get("trace") or []
    deadline = time.time() + 30
    while state in {"NEW", "INGESTING", "EXTRACTING", "queued"} and time.time() < deadline:
        time.sleep(0.4)
        status = client.get(f"/api/v1/cases/{case_id}")
        status.raise_for_status()
        _keep_session(client, status)
        state = status.json().get("state")
        events = client.get(f"/api/v1/cases/{case_id}/events")
        if events.status_code == 200:
            tools = [e.get("tool_name") for e in events.json().get("events", []) if e.get("tool_name")]
    print(f"HERO_SMOKE_PASS state={state} tools={len(tools)} trace={tools}")
    if state not in {"REVIEW_REQUIRED", "READY_FOR_ACTION", "WAITING_APPROVAL", "HANDOFF_READY"}:
        raise SystemExit(f"unexpected state {state}")


if __name__ == "__main__":
    main()
