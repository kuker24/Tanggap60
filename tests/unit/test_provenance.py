from __future__ import annotations

from app.services.extraction import decode_pages, encode_pages, extract_pdf_pages, locator_for
from app.services.facts import extract_candidates
from tests.fixture_render import invoice_pdf


def test_pdf_amount_on_page_two() -> None:
    pages = extract_pdf_pages(invoice_pdf())
    assert len(pages) == 2
    assert "INVOICE" in pages[0].text
    assert "Rp2.500.000" in pages[1].text
    found = extract_candidates(pages[1].text, page=2)
    amounts = [c for c in found if c.type.value == "AMOUNT"]
    assert amounts
    assert amounts[0].page == 2
    assert amounts[0].locator.startswith("p2:")


def test_account_regex_skips_invoice_noise() -> None:
    found = extract_candidates(
        "Invoice INV-DEMO-20260903 tujuan DEMO-DEST-A id DEMO-A-09013 DEMO-001",
        page=1,
    )
    accounts = [c.raw_value for c in found if c.type.value == "ACCOUNT"]
    assert "DEMO-DEST-A" in accounts
    assert "DEMO-A-09013" not in accounts
    assert "DEMO-001" not in accounts
    assert "DEMO-20260903" not in accounts


def test_page_roundtrip_locator() -> None:
    from app.services.extraction import PageText

    payload = encode_pages([PageText(page=3, text="Transfer Rp1.000 ke DEMO-DEST-01")])
    pages = decode_pages(payload)
    assert pages[0].page == 3
    loc = locator_for("Rp1.000", 3, 9, 16, [])
    assert loc == "p3:o9-16"
