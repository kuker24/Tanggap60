from __future__ import annotations

from fastapi.testclient import TestClient

from tests.hero_support import create_case


def test_cancelled_submit_never_locks_button(client: TestClient) -> None:
    js = client.get("/static/app.js").text
    assert "if (e.defaultPrevented) return;" in js
    harden = js.split('document.addEventListener("submit"', 1)[1].split("});", 1)[0]
    assert "setTimeout(() => {" in harden
    assert "if (e.defaultPrevented" in harden


def test_intake_tabs_support_arrow_keys(client: TestClient) -> None:
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/intake").text
    assert 'role="tab"' in page
    js = client.get("/static/app.js").text
    assert 'btn.tabIndex = on ? 0 : -1' in js
    assert '"ArrowRight"' in js and '"ArrowLeft"' in js
    assert '"Home"' in js and '"End"' in js


def test_help_panel_focus_and_escape_contract(client: TestClient) -> None:
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/intake").text
    assert 'id="agent-close"' in page
    js = client.get("/static/agent.js").text
    # Opening help focuses the close control, never the text input.
    assert 'getElementById("agent-close").focus({ preventScroll: true })' in js
    assert 'setTimeout(() => input.focus(), 50)' not in js
    # Escape exits the panel; small screens mark it modal with a Tab loop.
    assert 'if (ev.key === "Escape" && !panel.hidden)' in js
    assert 'panel.setAttribute("aria-modal", sheet ? "true" : "false")' in js
    assert 'aria-modal") !== "true") return' in js


def test_live_guidance_requires_explicit_opt_in(client: TestClient) -> None:
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/intake").text
    assert 'id="agent-autopilot" type="checkbox">' in page
    js = client.get("/static/agent.js").text
    assert "let guidanceOn = false" in js
    assert "if (guidanceOn && data.guidance_plan" in js
    assert "(actions || []).slice(0, 2)" in js
    assert 'prop.action_type === "OPEN_OFFICIAL"' in js


def test_help_composer_targets_and_layout(client: TestClient) -> None:
    css = client.get("/static/agent.css").text
    quick = css.split(".agent-quick button {", 1)[1].split("}", 1)[0]
    assert "min-height: 44px" in quick
    assert "min-width: 0" in css
