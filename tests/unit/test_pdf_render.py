from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.templates.pdf import render_lines


def test_pdf_has_brand_and_safety_labels() -> None:
    data = render_lines(
        "Untuk bank",
        [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "BELUM LENGKAP — PERLU TINDAKAN",
            "STATUS RESMI: NOT_VERIFIED",
            "Profile kesiapan 2026-09-02.mvp2",
            "## Ringkasan",
            "- Jumlah uang: Rp2.750.000 (Benar)",
        ],
        "2026-09-03T02:06:40",
        "abcd" * 16,
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    assert "SatuAman" in text
    assert "DRAF PENGGUNA" in text
    assert "NOT_VERIFIED" in text
    assert "BELUM LENGKAP" in text
    assert "Profile kesiapan" in text
    assert "Untuk bank" in text
    assert "case-" not in text
