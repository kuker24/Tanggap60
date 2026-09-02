from __future__ import annotations

import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "demo_tanggap60"


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    client = httpx.Client(base_url=base, timeout=45.0, follow_redirects=True)
    client.get("/health/live").raise_for_status()
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"})
    created.raise_for_status()
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
    run = client.post(
        f"/api/v1/cases/{case_id}/runs",
        headers={"Idempotency-Key": f"smoke-{uuid.uuid4().hex[:8]}"},
    )
    run.raise_for_status()
    body = run.json()
    tools = body.get("trace") or []
    print(
        f"HERO_SMOKE_PASS state={body.get('state')} tools={len(tools)} "
        f"hermes={body.get('hermes_mode')} trace={tools}"
    )
    if body.get("state") not in {"REVIEW_REQUIRED", "READY_FOR_ACTION", "WAITING_APPROVAL", "HANDOFF_READY"}:
        raise SystemExit(f"unexpected state {body.get('state')}")


if __name__ == "__main__":
    main()
