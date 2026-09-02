from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import build_container
from app.main import create_app
from app.services.extraction import TesseractOcr

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "demo_tanggap60"


def _tesseract_ready() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        TesseractOcr().recognize((FIXTURES / "01_chat.png").read_bytes())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not FIXTURES.exists() or not _tesseract_ready(), reason="tesseract/fixtures unavailable")


def test_tesseract_reads_hero_fixtures(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="test-secret-key-16",
        database_url=f"sqlite:////{tmp_path / 'db' / 't.db'}",
        case_storage_dir=tmp_path / "cases",
        resource_guard_enabled=False,
        sync_jobs=True,
        app_env="test",
        official_iasc_url="https://iasc.ojk.go.id/",
    )
    app = create_app(build_container(settings, ocr=TesseractOcr()))
    client = TestClient(app)
    expected = json.loads((FIXTURES / "expected_facts.json").read_text(encoding="utf-8"))
    case_id = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "AFTER_LOSS"}).json()["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[("files", ("chat.png", (FIXTURES / "01_chat.png").read_bytes(), "image/png"))],
    )
    client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[("files", ("transfer.png", (FIXTURES / "03_transfer.png").read_bytes(), "image/png"))],
    )
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "ocr-1"})
    assert run.status_code == 202
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    blob = " ".join(f["raw_value"] for f in facts)
    assert "2.500.000" in blob or "2500000" in blob
    assert "2.750.000" in blob or "2750000" in blob
    assert expected["account"] in blob
    pages = {f["source_page"] for f in facts if f["type"] == "AMOUNT"}
    assert pages
    assert all(f["source_locator"] for f in facts)
