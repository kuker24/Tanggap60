from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfReader

from tests.conftest import ScriptedOcr
from tests.hero_support import confirm_critical, create_case, upload_text_png

AMBIGUOUS = (
    "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB "
    "Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB"
)


def _facts_by_type(client: TestClient, case_id: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"ACCOUNT": [], "AMOUNT": [], "DATETIME": []}
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        grouped.setdefault(fact["type"], []).append(fact)
    return grouped


def _ambiguous_case(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", AMBIGUOUS)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "amb-run"})
    confirm_critical(client, case_id)
    return case_id


def test_case_approval_rejects_ambiguous(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    snap = draft.json()["snapshot_hash"]
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert any(u["mapping_status"] == "AMBIGUOUS" for u in units)
    denied = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "amb-appr"},
        json={"snapshot_hash": snap, "accepted_notice": True},
    )
    assert denied.status_code == 400
    page = client.get(f"/cases/{case_id}/approval")
    assert "Dokumen belum bisa dibuat" in page.text
    assert 'id="go"' not in page.text
    review = client.get(f"/cases/{case_id}/review")
    assert ">Simpan pilihan</button>" in review.text


def test_review_pairing_one_decision_per_card(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    page = client.get(f"/cases/{case_id}/review")
    assert "Cocokkan transaksi" in page.text
    assert "Jumlah uang mana yang dikirim ke" in page.text
    assert "Tidak ada" in page.text
    assert "pair-card" not in page.text
    assert "milik pelaku" not in page.text


def test_pairing_then_pack_omits_ids(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    unit = next(u for u in units if u["mapping_status"] == "AMBIGUOUS")
    grouped = _facts_by_type(client, case_id)
    dest_a = next(f for f in grouped["ACCOUNT"] if "DEST-A" in f["raw_value"])
    dest_b = next(f for f in grouped["ACCOUNT"] if "DEST-B" in f["raw_value"])
    amt_a = next(f for f in grouped["AMOUNT"] if "2.000" in f["raw_value"] or f.get("normalized_value") == 2000000)
    amt_b = next(f for f in grouped["AMOUNT"] if "750" in f["raw_value"])
    time_a = next(f for f in grouped["DATETIME"] if "09:13" in f["raw_value"])
    time_b = next(f for f in grouped["DATETIME"] if "09:47" in f["raw_value"])
    mapped = client.post(
        f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping",
        json={
            "target_evidence_id": unit["evidence_ids"][0],
            "pairings": [
                {
                    "destination_fact_id": dest_a["fact_id"],
                    "amount_fact_id": amt_a["fact_id"],
                    "datetime_fact_id": time_a["fact_id"],
                },
                {
                    "destination_fact_id": dest_b["fact_id"],
                    "amount_fact_id": amt_b["fact_id"],
                    "datetime_fact_id": time_b["fact_id"],
                },
            ],
        },
    )
    assert mapped.status_code == 200
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    ok = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "pair-appr"},
        json={"snapshot_hash": draft.json()["snapshot_hash"], "accepted_notice": True},
    )
    assert ok.status_code == 200, ok.text
    arts = client.get(f"/api/v1/cases/{case_id}/artifacts").json()["artifacts"]
    assert all(a["verify_status"] == "PASS" for a in arts)
    zip_art = next(a for a in arts if a["type"] == "CASE_ZIP")
    packed = zipfile.ZipFile(
        io.BytesIO(client.get(f"/api/v1/cases/{case_id}/artifacts/{zip_art['artifact_id']}/download").content)
    )
    names = set(packed.namelist())
    bank_names = [n for n in names if n.endswith("bank_handoff_pack.pdf")]
    assert bank_names
    bank_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(packed.read(bank_names[0]))).pages)
    assert "DEMO-DEST-A" in bank_text or "DEMO-DEST-B" in bank_text
    assert "2.000.000" in bank_text or "750.000" in bank_text
    assert "ru_" not in bank_text
    assert "fact-" not in bank_text
    assert "AMBIGUOUS" not in bank_text
    plan_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(packed.read("action_plan.pdf"))).pages)
    assert "Hubungi bank" in plan_text
    assert "ru_" not in plan_text


CLEAN = "Transfer Berhasil Rp500.000 Ke: DEMO-DEST-C 23 September 2026 10:05 WIB Dari: DEMO-VICTIM-MASKED"


def test_mixed_units_guide_but_block_partial_pack(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "clean.png", CLEAN)
    upload_text_png(client, ocr, case_id, "ambiguous.png", AMBIGUOUS)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "mix-run"})
    confirm_critical(client, case_id)
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert any(u["mapping_status"] == "AMBIGUOUS" for u in units)
    nxt = client.get(f"/api/v1/cases/{case_id}/next-action")
    assert nxt.status_code == 200
    assert nxt.json().get("code")
    ready = next((u for u in units if u["mapping_status"] == "COMPLETE"), units[0])
    denied_unit = client.post(
        f"/api/v1/cases/{case_id}/reporting-units/{ready['unit_id']}/approval",
        headers={"Idempotency-Key": "unit-appr"},
        json={"snapshot_hash": "x", "accepted_notice": True},
    )
    assert denied_unit.status_code == 404
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    if draft.status_code == 200:
        blocked = client.post(
            f"/api/v1/cases/{case_id}/approval",
            headers={"Idempotency-Key": "mix-appr"},
            json={"snapshot_hash": draft.json()["snapshot_hash"], "accepted_notice": True},
        )
        assert blocked.status_code == 400
    page = client.get(f"/cases/{case_id}/readiness")
    assert "Buat paket untuk" not in page.text
    assert "Pasangkan" in page.text or "pasang" in page.text.lower()
