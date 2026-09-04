from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import build_container
from app.main import create_app
from tests.hero_support import create_case


def test_case_responses_are_uncacheable(client: TestClient) -> None:
    case_id = create_case(client)
    res = client.get(f"/api/v1/cases/{case_id}")
    assert "no-store" in res.headers.get("Cache-Control", "")


def test_metrics_hidden_outside_test(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="test-secret-key-16",
        database_url=f"sqlite:////{tmp_path / 'db' / 't.db'}",
        case_storage_dir=tmp_path / "cases",
        resource_guard_enabled=False,
        sync_jobs=True,
        app_env="competition",
        official_iasc_url="https://iasc.ojk.go.id/",
    )
    client = TestClient(create_app(build_container(settings)))
    assert client.get("/demo/metrics").status_code == 404


def test_production_cookie_is_secure(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="test-secret-key-16",
        database_url=f"sqlite:////{tmp_path / 'db' / 'p.db'}",
        case_storage_dir=tmp_path / "pcases",
        resource_guard_enabled=False,
        sync_jobs=True,
        app_env="production",
        official_iasc_url="https://iasc.ojk.go.id/",
    )
    client = TestClient(create_app(build_container(settings)))
    res = client.get("/health/live")
    assert "Secure" in res.headers.get("set-cookie", "")


def test_agent_and_receipt_clear_browser_storage() -> None:
    root = Path(__file__).resolve().parents[2]
    agent = (root / "app/web/static/agent.js").read_text(encoding="utf-8")
    receipt = (root / "app/web/templates/receipt.html").read_text(encoding="utf-8")
    assert "sessionStorage.removeItem(HIST_KEY)" in agent
    assert "sessionStorage.removeItem(PLAN_KEY)" in agent
    assert "t60agent:" in receipt
    assert "sessionStorage.removeItem" in receipt
