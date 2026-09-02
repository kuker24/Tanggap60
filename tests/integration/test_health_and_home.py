from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["status"] == "ready"


def test_home_buttons(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Belum ada kerugian" in page.text
    assert "Sudah terjadi kerugian" in page.text


def test_metrics_no_evidence(client: TestClient) -> None:
    data = client.get("/demo/metrics").json()
    assert "process_rss_mb" in data
