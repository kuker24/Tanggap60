from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_landing_is_light_and_case_is_calm_light(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'content="light"' in home.text
    assert 'content="#ffffff"' in home.text
    assert "/static/app.css?v=" in home.text
    assert "is-landing" in home.text
    assert "c1-container" in home.text
    assert "c1-card" in home.text
    assert "Saya sudah kirim uang" in home.text
    assert "Saya baru curiga, belum kirim uang" in home.text
    assert "choice-arrow" not in home.text
    assert "cheerful cartoon" not in home.text
    case_id = create_case(client)
    for path in (
        "intake",
        "processing",
        "review",
        "readiness",
        "result",
        "approval",
        "artifacts",
        "receipt",
    ):
        page = client.get(f"/cases/{case_id}/{path}")
        assert page.status_code == 200, path
        assert 'content="light"' in page.text, path
        assert 'content="#faf7f1"' in page.text, path
        assert "is-landing" not in page.text, path
    intake = client.get(f"/cases/{case_id}/intake")
    assert 'id="files"' in intake.text
    assert 'id="text"' in intake.text
    assert 'id="url"' in intake.text
    assert "coach-step" in intake.text
    assert "Pilih foto atau file" in intake.text
    processing = client.get(f"/cases/{case_id}/processing")
    assert "wait-ring" in processing.text
    assert "timeline" in processing.text
    assert "Lihat rincian proses" in processing.text
    assert "Mengelompokkan bukti per transaksi." in processing.text
    review = client.get(f"/cases/{case_id}/review")
    assert "fact-grid" in review.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "#faf7f1" in css.text
    assert "--amber" in css.text
    assert "--aurora" not in css.text
    assert "body.is-landing" in css.text
    assert ".c1-card" in css.text
    assert "#100904" not in css.text
    assert "#FFFEFB" not in css.text
    assert "#fffefb" not in css.text.lower()
    assert "prefers-reduced-motion" in css.text
    assert "@keyframes spin" in css.text
    assert "coach-enabled" in css.text
    assert ".btn-text" in css.text
    assert ".actions" in css.text
    net = client.get("/static/landing/network.svg")
    lib = client.get("/static/landing/library.svg")
    assert net.status_code == 200
    assert lib.status_code == 200
