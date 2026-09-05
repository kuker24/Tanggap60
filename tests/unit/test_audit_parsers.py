from __future__ import annotations

from app.domain.models import FactType
from app.domain.policies import normalize_amount, normalize_datetime, route_from_condition
from app.domain.states import DeclaredCondition, Route
from app.services.facts import extract_candidates
from app.services.routing import apply_route, infer_loss
from app.services.urlcheck import analyze_url
from tests.unit.test_reporting_units import _fact


def test_before_loss_with_chat_amount_stays_preincident() -> None:
    found = extract_candidates("Kirim dulu Rp2.500.000, saya belum transfer.")
    amounts = [c for c in found if c.type.value == "AMOUNT"]
    assert amounts
    assert amounts[0].normalized_value == "2500000"
    facts = [
        _fact("f-amt", FactType.AMOUNT, "Rp2.500.000", "2500000", "ev-chat"),
    ]
    assert infer_loss(facts) is True
    route, _reason, _conf, ask = apply_route(DeclaredCondition.BEFORE_LOSS, facts)
    assert route == Route.PRE_INCIDENT_CHECK
    assert ask is True
    routed, _, _, ask2 = route_from_condition(DeclaredCondition.BEFORE_LOSS, has_loss_facts=True)
    assert routed == Route.PRE_INCIDENT_CHECK
    assert ask2 is True


def test_january_2024_wib_not_demo_date() -> None:
    value = normalize_datetime("4 Januari 2024 08:42 WIB")
    assert value == "2024-01-04T01:42:00Z"
    found = extract_candidates("Transfer 4 Januari 2024 08:42 WIB")
    dates = [c.normalized_value for c in found if c.type.value == "DATETIME"]
    assert dates == ["2024-01-04T01:42:00Z"]


def test_numeric_date_dmy() -> None:
    assert normalize_datetime("04/09/2026 09:10") == "2026-09-04T09:10:00"


def test_date_only_has_no_fake_time() -> None:
    assert normalize_datetime("4 Januari 2024") == "2024-01-04"


def test_indonesian_amount_correction() -> None:
    assert normalize_amount("Rp2.750.000,00") == "2750000"
    assert normalize_amount("Rp2.750.000") == "2750000"


def test_foreign_amount_not_truncated() -> None:
    found = extract_candidates("Bayar Rp 2,750,000 sekarang")
    amounts = [c.normalized_value for c in found if c.type.value == "AMOUNT"]
    assert amounts == ["2750000"]


def test_phone_is_not_destination_account() -> None:
    found = extract_candidates("Hubungi 081234567890")
    types = {(c.type.value, c.raw_value) for c in found}
    assert ("PHONE", "081234567890") in types
    assert ("ACCOUNT", "081234567890") not in types


def test_invalid_url_ports_do_not_raise() -> None:
    indicators, fetched = analyze_url("https://example.com:abc")
    assert fetched is False
    assert any(i.name == "port_tidak_valid" for i in indicators)
    indicators2, fetched2 = analyze_url("https://example.com:99999")
    assert fetched2 is False
    assert any(i.name == "port_tidak_valid" for i in indicators2)


def test_url_path_case_preserved() -> None:
    found = extract_candidates("lihat https://Example.COM/Path/File")
    urls = [c for c in found if c.type.value == "URL"]
    assert urls
    assert urls[0].normalized_value == "https://example.com/Path/File"


def test_image_pixels_rejected_from_header() -> None:
    from io import BytesIO

    import pytest
    from PIL import Image

    from app.domain.errors import InvalidFileType
    from app.services.intake import check_image_pixels

    buf = BytesIO()
    Image.new("RGB", (80, 80), "white").save(buf, format="PNG")
    check_image_pixels(buf.getvalue(), 20_000_000)
    big = BytesIO()
    Image.new("RGB", (80, 80), "white").save(big, format="PNG")
    with pytest.raises(InvalidFileType):
        check_image_pixels(big.getvalue(), 100)


def test_read_upload_limited_caps_bytes() -> None:
    import asyncio

    import pytest

    from app.domain.errors import UploadLimitExceeded
    from app.services.intake import read_upload_limited

    class _Up:
        def __init__(self) -> None:
            self.n = 0

        async def read(self, n: int) -> bytes:
            self.n += 1
            if self.n > 4:
                return b""
            return b"x" * n

    with pytest.raises(UploadLimitExceeded):
        asyncio.run(read_upload_limited(_Up(), 100))
