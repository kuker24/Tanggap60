from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.domain.policies import sha256_bytes
from tests.conftest import ScriptedOcr
from tests.fixture_render import image_only_pdf, mixed_text_and_image_pdf, png_bytes
from tests.hero_support import create_case


def test_image_only_pdf_yields_amount(client: TestClient, ocr: ScriptedOcr) -> None:
    text = "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A"
    pdf = image_only_pdf(png_bytes(text))
    reader = PdfReader(BytesIO(pdf))
    assert not any((page.extract_text() or "").strip() for page in reader.pages)
    for page in reader.pages:
        for image in page.images:
            ocr.by_hash[sha256_bytes(image.data)] = text
    case_id = create_case(client)
    uploaded = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[("files", ("bukti.pdf", pdf, "application/pdf"))],
    )
    assert uploaded.status_code == 202
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "pdf-ocr-1"})
    assert run.status_code == 202
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    blob = " ".join(f["raw_value"] for f in facts)
    assert "2.000.000" in blob or "2000000" in blob
    assert "DEMO-DEST-A" in blob


def test_mixed_pdf_ocrs_scan_page(client: TestClient, ocr: ScriptedOcr) -> None:
    scan = "Transfer Berhasil Rp3.100.000 Ke: DEMO-DEST-MIX"
    pdf = mixed_text_and_image_pdf(png_bytes(scan), "Halaman teks: percakapan biasa tanpa nominal")
    reader = PdfReader(BytesIO(pdf))
    assert (reader.pages[0].extract_text() or "").strip()
    assert not (reader.pages[1].extract_text() or "").strip()
    for image in reader.pages[1].images:
        ocr.by_hash[sha256_bytes(image.data)] = scan
    case_id = create_case(client)
    uploaded = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files=[("files", ("campuran.pdf", pdf, "application/pdf"))],
    )
    assert uploaded.status_code == 202
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "pdf-mix-1"})
    assert run.status_code == 202
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    blob = " ".join(f["raw_value"] for f in facts)
    assert "3.100.000" in blob or "3100000" in blob
