from __future__ import annotations

import os
import statistics
import time

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import CHAT, TRANSFER, approve_to_handoff, create_case, resolve_amount_conflict, upload_text_png


def test_hero_pipeline_p95_under_60s(client: TestClient, ocr: ScriptedOcr) -> None:
    runs = int(os.environ.get("TANGGAP60_SOAK", "10"))
    times: list[float] = []
    for index in range(runs):
        started = time.perf_counter()
        case_id = create_case(client)
        upload_text_png(client, ocr, case_id, "chat.png", CHAT)
        upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
        run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": f"perf-{index}"})
        assert run.status_code == 202
        resolve_amount_conflict(client, case_id)
        body = approve_to_handoff(client, case_id)
        assert body["state"] == "HANDOFF_READY"
        times.append(time.perf_counter() - started)
    times.sort()
    p95 = times[max(0, int(round(0.95 * (len(times) - 1))))]
    p50 = statistics.median(times)
    assert p50 < 60
    assert p95 < 60
    assert max(times) < 60
