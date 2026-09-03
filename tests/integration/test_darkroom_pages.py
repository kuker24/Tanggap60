from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_every_victim_page_is_darkroom(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'content="dark"' in home.text
    assert 'content="#100904"' in home.text
    assert "oryzo-inner" in home.text
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
        assert 'content="#100904"' in page.text, path
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "#100904" in css.text
    assert "#FFFEFB" not in css.text
    assert "#fffefb" not in css.text.lower()
