from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.domain.policies import sha256_bytes
from tests.conftest import ScriptedOcr
from tests.fixture_render import image_only_pdf, png_bytes
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
