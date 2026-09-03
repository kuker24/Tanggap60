from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_every_victim_page_is_darkroom(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'content="dark"' in home.text
    assert 'content="#000000"' in home.text
    assert "aurora-center" in home.text
    assert "aurora" in home.text
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
        assert 'content="dark"' in page.text, path
        assert 'content="#000000"' in page.text, path
    intake = client.get(f"/cases/{case_id}/intake")
    assert 'id="files"' in intake.text
    assert 'id="text"' in intake.text
    assert 'id="url"' in intake.text
    assert "coach-step" in intake.text
    assert "Pilih berkas" in intake.text
    assert "choice-arrow" not in home.text
    processing = client.get(f"/cases/{case_id}/processing")
    assert "wait-ring" in processing.text
    assert "is-void" in processing.text
    review = client.get(f"/cases/{case_id}/review")
    assert "fact-grid" in review.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "#000000" in css.text
    assert "--aurora" in css.text
    assert "#100904" not in css.text
    assert "#FFFEFB" not in css.text
    assert "#fffefb" not in css.text.lower()
    assert "prefers-reduced-motion" in css.text
    assert "@keyframes spin" in css.text
    assert "coach-enabled" in css.text
    assert ".btn-text" in css.text
    assert ".actions" in css.text
