from __future__ import annotations

import re

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_landing_is_light_and_case_is_calm_light(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'content="light"' in home.text
    assert 'content="#faf7f1"' in home.text
    assert "/static/app.css?v=" in home.text
    assert "is-landing" in home.text
    assert "land-how" in home.text
    assert "land-steps" in home.text
    assert "Sudah transfer" in home.text
    assert "Belum transfer" in home.text
    assert "choice-arrow" not in home.text
    assert "cheerful cartoon" not in home.text
    case_id = create_case(client)
    for path in (
        "intake",
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
    assert "evidence-composer" in intake.text
    assert 'role="tab"' not in intake.text
    assert "Pilih foto atau PDF" in intake.text
    assert "Teks chat" in intake.text
    assert "Masukkan link" in intake.text
    assert "Kirim bukti yang ada" in intake.text
    fixture = intake.text.find('id="btn-demo-two-amounts"')
    panel = intake.text.find('id="panel-text"')
    assert fixture != -1 and panel != -1 and fixture < panel
    assert "demo-fixture" in intake.text
    assert 'style="font-size:0.875rem"' not in intake.text
    empty_proc = client.get(f"/cases/{case_id}/processing", follow_redirects=False)
    assert empty_proc.status_code == 303
    assert empty_proc.headers["location"].endswith("/intake")
    review = client.get(f"/cases/{case_id}/review")
    assert "fact-grid" in review.text
    assert "Belum ada data" in review.text
    assert "Lanjut dulu" not in review.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "#faf7f1" in css.text
    assert "--amber" in css.text
    assert "--aurora" not in css.text
    assert "body.is-landing" in css.text
    assert ".land-steps" in css.text
    assert "#100904" not in css.text
    assert "#FFFEFB" not in css.text
    assert "#fffefb" not in css.text.lower()
    assert "prefers-reduced-motion" in css.text
    assert "@keyframes spin" in css.text
    assert "composer-enabled" in css.text
    assert ".btn-text" in css.text
    assert ".actions" in css.text


def test_route_aliases_confirm_and_act(client: TestClient) -> None:
    case_id = create_case(client)
    res_confirm = client.get(f"/cases/{case_id}/confirm", follow_redirects=False)
    assert res_confirm.status_code == 303
    assert res_confirm.headers["location"].endswith(f"/cases/{case_id}/review")

    res_act = client.get(f"/cases/{case_id}/act", follow_redirects=False)
    assert res_act.status_code == 303
    assert res_act.headers["location"].endswith(f"/cases/{case_id}/readiness")


def test_demo_two_amounts_conflict_flow(client: TestClient) -> None:
    # 1. Start DEMO case
    started = client.post("/start", data={"declared_condition": "AFTER_LOSS", "mode": "DEMO"}, follow_redirects=False)
    assert started.status_code == 303
    case_url = started.headers["location"]
    case_id = case_url.split("/cases/")[1].split("/")[0]

    intake_page = client.get(f"/cases/{case_id}/intake")
    assert 'id="btn-demo-two-amounts"' in intake_page.text
    assert intake_page.text.find('id="btn-demo-two-amounts"') < intake_page.text.find('id="panel-text"')

    standard = client.post("/start", data={"declared_condition": "AFTER_LOSS", "mode": "STANDARD"}, follow_redirects=False)
    assert standard.status_code == 303
    standard_id = standard.headers["location"].split("/cases/")[1].split("/")[0]
    standard_intake = client.get(f"/cases/{standard_id}/intake")
    assert 'id="btn-demo-two-amounts"' not in standard_intake.text

    # 2. Intake with demo two amounts
    intake_res = client.post(
        f"/cases/{case_id}/intake",
        data={"load_fixture": "two_amounts"},
        follow_redirects=False,
    )
    assert intake_res.status_code == 303
    assert intake_res.headers["location"].endswith(f"/cases/{case_id}/processing")

    # 3. Processing page has real status and explicit button
    proc_page = client.get(f"/cases/{case_id}/processing")
    assert proc_page.status_code == 200
    assert "btn-to-review" in proc_page.text

    # 4. Review page immediately contains facts and conflict card
    review_page = client.get(f"/cases/{case_id}/review")
    assert review_page.status_code == 200
    assert "Mana yang benar?" in review_page.text
    assert "Rp2.750.000" in review_page.text
    assert "Rp2.500.000" in review_page.text
    assert "Dari struk" in review_page.text
    assert "Dari chat" in review_page.text
    assert "cerita.txt" not in review_page.text
    sources = re.findall(
        r"<b>(Rp[\d.]+)</b>.*?<span class=\"muted small\">(Dari [^<]+)</span>",
        review_page.text,
        re.S,
    )
    assert ("Rp2.750.000", "Dari struk") in sources
    assert ("Rp2.500.000", "Dari chat") in sources
    assert "Belum ada data" not in review_page.text

    # 5. Stepper: Step 2 (Periksa) is done (not skipped) when evidence exists
    assert 'class="step skipped"><i aria-hidden="true">2</i> Periksa' not in review_page.text
    assert 'Periksa' in review_page.text

