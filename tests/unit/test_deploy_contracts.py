from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_install_hermes_masks_public_tunnel() -> None:
    text = (ROOT / "scripts/install_hermes_dashboard.sh").read_text(encoding="utf-8")
    assert "systemctl start hermes-tunnel" not in text
    assert "systemctl enable hermes-dashboard hermes-tunnel" not in text
    assert "systemctl mask hermes-tunnel" in text
    assert "ssh -L 9119:127.0.0.1:9119" in text.lower() or "127.0.0.1:9119" in text


def test_verify_vps_fails_on_active_hermes_tunnel() -> None:
    text = (ROOT / "scripts/verify_vps.sh").read_text(encoding="utf-8")
    assert "HERMES_TUNNEL_MUST_BE_INACTIVE" in text
    assert "hermes-tunnel" in text
    assert "node --check" in text
    assert "rev-parse" in text
    assert "bash -n" in text


def test_hermes_tunnel_unit_does_not_start_cloudflared() -> None:
    text = (ROOT / "deploy/hermes-tunnel.service").read_text(encoding="utf-8")
    assert "cloudflared" not in text
    assert "9119" in text
