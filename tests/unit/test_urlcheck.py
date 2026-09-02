from __future__ import annotations

from app.services.urlcheck import analyze_url


def test_localhost_not_fetched() -> None:
    indicators, fetched = analyze_url("http://127.0.0.1/admin")
    assert fetched is False
    assert any(i.name == "alamat_lokal" for i in indicators)


def test_metadata_ip() -> None:
    indicators, fetched = analyze_url("http://169.254.169.254/latest")
    assert fetched is False
    assert any(i.name == "alamat_lokal" for i in indicators)
