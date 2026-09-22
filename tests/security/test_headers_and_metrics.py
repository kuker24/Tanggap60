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
    # HTTPS requests in production/competition MUST receive Secure cookie
    client_https = TestClient(create_app(build_container(settings)), base_url="https://testserver")
    res = client_https.get("/health/live")
    assert "Secure" in res.headers.get("set-cookie", "")

    # Reverse-proxy HTTPS forwarding (e.g. Cloudflare tunnel) MUST also receive Secure cookie
    client_http = TestClient(create_app(build_container(settings)), base_url="http://testserver")
    res_proxy = client_http.get("/health/live", headers={"x-forwarded-proto": "https"})
    assert "Secure" in res_proxy.headers.get("set-cookie", "")

    # Non-TLS HTTP in production sets secure=False (with warning) so browser doesn't drop session
    res_plain = client_http.get("/health/live")
    cookie_plain = res_plain.headers.get("set-cookie", "")
    assert "t60_sid" in cookie_plain
    assert "Secure" not in cookie_plain


def test_dev_http_session_cookie_works(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="test-secret-key-16",
        database_url=f"sqlite:////{tmp_path / 'db' / 'd.db'}",
        case_storage_dir=tmp_path / "dcases",
        resource_guard_enabled=False,
        sync_jobs=True,
        app_env="development",
        official_iasc_url="https://iasc.ojk.go.id/",
    )
    client = TestClient(create_app(build_container(settings)), base_url="http://127.0.0.1:8000")
    res = client.post("/start", data={"declared_condition": "AFTER_LOSS", "mode": "DEMO"}, follow_redirects=False)
    assert res.status_code == 303
    cookie = res.headers.get("set-cookie", "")
    assert "t60_sid" in cookie
    assert "Secure" not in cookie
    location = res.headers["location"]
    res2 = client.get(location)
    assert res2.status_code == 200


def test_head_routes_return_ok(client: TestClient) -> None:
    assert client.head("/").status_code == 200
    assert client.head("/health/live").status_code == 200
    assert client.head("/health/ready").status_code == 200


def test_agent_and_receipt_clear_browser_storage() -> None:
    root = Path(__file__).resolve().parents[2]
    agent = (root / "app/web/static/agent.js").read_text(encoding="utf-8")
    app = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    base = (root / "app/web/templates/base.html").read_text(encoding="utf-8")
    data_controls = (root / "app/web/templates/_data_controls.html").read_text(encoding="utf-8")
    assert "sessionStorage.removeItem(HIST_KEY)" in agent
    assert "sessionStorage.removeItem(PLAN_KEY)" in agent
    assert "window.t60Purge" in app
    assert 'sessionStorage.removeItem("t60agent:" + id)' in app
    assert 'sessionStorage.removeItem("t60plan:" + id)' in app
    assert '{% include "_data_controls.html" %}' in base
    assert 'id="purge-browser-form"' in data_controls
