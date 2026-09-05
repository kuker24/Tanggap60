from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import TRANSFER, approve_to_handoff, create_case, upload_text_png


@pytest.fixture
def handoff_case(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", TRANSFER)
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "handoff-ux-run"})
    assert run.status_code == 202
    approve_to_handoff(client, case_id)
    return case_id


# "result" 303-redirects to readiness for AFTER_LOSS cases; the client follows
# it, so this also pins the readiness handoff contract.
@pytest.mark.parametrize("surface", ["workspace", "artifacts", "receipt", "result"])
def test_manual_document_handoff_contract(client: TestClient, handoff_case: str, surface: str) -> None:
    page = client.get(f"/cases/{handoff_case}/{surface}")
    assert page.status_code == 200
    content = page.text.split('<main id="main"', 1)[1].split("</main>", 1)[0]
    instructions = content.lower()
    pdf = instructions.index("unduh ringkasan pdf")
    official = instructions.index("situs resmi", pdf)
    copy = instructions.index("salin", official)
    assert pdf < official < copy
    assert "lampirkan" in instructions[copy:]
    assert "formulir" in instructions[copy:]
    assert "tidak menggantikan bukti asli" in instructions
    assert "zip" in instructions and "opsional" in instructions
    assert "file atau folder downloads (unduhan)" in instructions
    assert "ekstrak" in instructions
    assert "tidak mengirim laporan" in instructions or "laporan dikirim oleh anda" in instructions
    if surface == "artifacts":
        assert "/download" in content
        assert f'/cases/{handoff_case}/workspace"' in content
        assert "bukan laporan yang otomatis diterima situs resmi" in instructions


def test_workspace_copy_and_load_recovery_contract(client: TestClient) -> None:
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/workspace")
    assert page.status_code == 200
    assert 'id="ws-empty"' in page.text
    assert 'id="ws-error"' in page.text
    assert "Ini bukan berarti transaksi Anda kosong" in page.text
    assert 'if (!response.ok) throw' in page.text
    assert 'retry.addEventListener("click", loadWorkspace)' in page.text
    assert "replaceChildren()" in page.text
    assert "navigator.clipboard.writeText(text)" in page.text
    assert 'button.addEventListener("click", () => copyText(value, k))' in page.text
    assert 'copyText(chronology, "Kronologi")' in page.text
    assert 'value.startsWith("Belum ")' in page.text
    assert 'id="ws-copy-status" role="status" aria-live="polite"' in page.text
    assert "Salin otomatis gagal" in page.text
    assert 'id="ws-copy-text" rows="5" readonly' in page.text
    assert "input.value = text" in page.text
    assert "input.select()" in page.text
    script = page.text.split("const CASE =", 1)[1].split("</script>", 1)[0]
    assert "innerHTML" not in script
    assert "textContent" in script
    failure = script.split("} catch (_) {", 2)[2]
    assert 'getElementById("ws-empty").hidden = false' not in failure
    assert "retry.disabled = false" in failure


def test_receipt_edit_feedback_preserves_input_and_purge_recovers(client: TestClient, handoff_case: str) -> None:
    saved = client.post(
        f"/api/v1/cases/{handoff_case}/receipt",
        headers={"Idempotency-Key": "handoff-ux-receipt"},
        json={"ticket_text": "IASC123456"},
    )
    assert saved.status_code == 200
    assert saved.json()["official_status"] == "NOT_VERIFIED"
    page = client.get(f"/cases/{handoff_case}/receipt")
    assert page.status_code == 200
    assert 'aria-describedby="rcpt-fix-alert"' in page.text
    assert 'id="rcpt-fix-alert" role="status" aria-live="polite"' in page.text
    edit = page.text.split('document.getElementById("rcpt-fix").addEventListener', 1)[1].split("</script>", 1)[0]
    assert "Perubahan belum tersimpan" in edit
    assert "Koneksi terputus" in edit
    assert "Coba simpan lagi" in edit
    assert "finally" in edit and "btn.disabled = false" in edit
    assert "purge-alert" not in edit
    assert '.value =' not in edit
    assert "bukan pelacak resmi" in page.text
    purge = page.text.split("async function purgeNow", 1)[1].split("</script>", 1)[0]
    assert 'if (!confirm(' in purge
    assert 'confirmation:"PURGE"' in purge
    assert "Penghapusan belum dapat dipastikan" in purge
    assert "finally" in purge and "btn.disabled = false" in purge
    assert 'sessionStorage.removeItem("t60agent:" + id)' in purge


def test_preincident_decision_recovers_without_iasc_legitimacy_claim(client: TestClient) -> None:
    created = client.post("/api/v1/cases", json={"mode": "DEMO", "declared_condition": "BEFORE_LOSS"})
    assert created.status_code == 201
    case_id = created.json()["case_id"]
    added = client.post(f"/api/v1/cases/{case_id}/evidence/text", json={"url": "https://example.com/offer"})
    assert added.status_code == 202
    run = client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "handoff-ux-pre"})
    assert run.status_code == 202
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        if fact["review_status"] == "CANDIDATE":
            confirmed = client.patch(
                f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}",
                json={"action": "confirm", "expected_version": version},
            )
            assert confirmed.status_code == 200
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    assert client.post(f"/api/v1/cases/{case_id}/draft").status_code == 200
    page = client.get(f"/cases/{case_id}/result")
    assert page.status_code == 200
    assert 'id="decision-next" class="notice" role="status"' in page.text
    assert "bukan layanan untuk memastikan tawaran atau situs itu sah" in page.text
    assert "masukkan datanya di sana" not in page.text
    assert "Pilihan belum tercatat" in page.text
    assert "Koneksi terputus" in page.text
    assert "} finally {" in page.text
    assert "buttons.forEach(button => { button.disabled = false; })" in page.text
    assert "Setujui dan buat paket" not in page.text
