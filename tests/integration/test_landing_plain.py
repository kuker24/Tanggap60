from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


def test_landing_has_three_meaningful_ordered_steps(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    steps = re.search(r'<ol class="land-steps">(.*?)</ol>', page.text, re.S)
    assert steps is not None
    items = re.findall(r"<li>(.*?)</li>", steps.group(1), re.S)
    assert len(items) == 3
    for item, heading in zip(items, ["Kirim bukti", "Cek data", "Lihat langkah"], strict=True):
        assert f"<h3>{heading}</h3>" in item
        assert re.search(r"<p>[^<]+</p>", item)
    assert "Kami tanya Anda. Kami tidak menebak." in page.text
    assert "Dari struk" in page.text
    assert "Dari chat" in page.text


def test_landing_is_plain_and_preserves_limits(client: TestClient) -> None:
    html = client.get("/").text
    for obsolete in ("\u2726", "c1-", "land-mock-cursor", "/static/landing/network.svg", "/static/landing/library.svg"):
        assert obsolete not in html
    for claim in (
        "Tidak mengirim laporan untuk Anda.",
        "Kami tidak menebak.",
        "Kasus tidak bisa dibuka setelah 60 menit.",
        "Tidak ada yang bisa memastikan uang kembali.",
        "Keputusan resmi tetap dari bank, IASC, atau polisi.",
        "Anda juga bisa menghapusnya sendiri.",
    ):
        assert claim in html
    assert html.count("<details>") == 3
    assert "Apa isi paketnya?" not in html


@pytest.mark.parametrize("condition", ["AFTER_LOSS", "BEFORE_LOSS"])
def test_landing_start_forms_keep_both_paths(client: TestClient, condition: str) -> None:
    html = client.get("/").text
    forms = re.findall(r'<form[^>]+action="/start"[^>]*>(.*?)</form>', html, re.S)
    assert len(forms) == 1
    assert 'name="mode" value="DEMO"' in forms[0]
    assert f'name="declared_condition" value="{condition}" type="submit"' in forms[0]
    started = client.post("/start", data={"mode": "DEMO", "declared_condition": condition}, follow_redirects=False)
    assert started.status_code == 303
    intake = client.get(started.headers["location"])
    assert intake.status_code == 200
    if condition == "BEFORE_LOSS":
        assert 'data-default-tab="text"' in intake.text


def test_styles_remove_landing_decorations_and_reduce_control_motion(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    css = response.text
    for obsolete in ("c1-", "land-eyebrow", "land-mock-cursor", "land-in", "land-float"):
        assert obsolete not in css
    reduced = css.split("@media (prefers-reduced-motion:reduce){", 1)[1].split("html:has", 1)[0]
    assert "transition-duration:.01ms!important" in reduced
    assert "animation-iteration-count:1!important" in reduced
    assert ".btn,.choice,#toast{transition:none}" in reduced
    assert ".btn:active,.choice:active{transform:none}" in reduced
    assert "#toast,#toast.show{transform:translateX(-50%)}" in reduced
    assert "(hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference)" in css
