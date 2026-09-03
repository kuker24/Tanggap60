from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import confirm_critical, create_case, upload_text_png

AMBIGUOUS = (
    "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB "
    "Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB"
)
CLEAN = "Transfer Berhasil Rp500.000 Ke: DEMO-DEST-C 23 September 2026 10:05 WIB Dari: DEMO-VICTIM-MASKED"


def _ambiguous_case(client: TestClient, ocr: ScriptedOcr) -> str:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "transfer.png", AMBIGUOUS)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "p0-run"})
    confirm_critical(client, case_id)
    return case_id


def _facts(client: TestClient, case_id: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"ACCOUNT": [], "AMOUNT": [], "DATETIME": []}
    for fact in client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]:
        grouped.setdefault(fact["type"], []).append(fact)
    return grouped


def _valid_payload(client: TestClient, case_id: str, unit: dict) -> dict:
    grouped = _facts(client, case_id)
    dest = next(f for f in grouped["ACCOUNT"] if "DEST-A" in f["raw_value"])
    amt = next(f for f in grouped["AMOUNT"] if "2.000" in f["raw_value"])
    return {
        "target_evidence_id": unit["evidence_ids"][0],
        "pairings": [{"destination_fact_id": dest["fact_id"], "amount_fact_id": amt["fact_id"]}],
    }


def _ambiguous_unit(client: TestClient, case_id: str) -> dict:
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    return next(u for u in units if u["mapping_status"] == "AMBIGUOUS")


def test_mapping_rejects_unknown_unit(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    payload = _valid_payload(client, case_id, unit)
    res = client.post(f"/api/v1/cases/{case_id}/reporting-units/ru_tidak_ada/mapping", json=payload)
    assert res.status_code == 400


def test_mapping_rejects_cross_case_fact(client: TestClient, ocr: ScriptedOcr) -> None:
    case_a = _ambiguous_case(client, ocr)
    case_b = create_case(client)
    upload_text_png(client, ocr, case_b, "clean.png", CLEAN)
    client.post(f"/api/v1/cases/{case_b}/runs", headers={"Idempotency-Key": "p0-run-b"})
    foreign = _facts(client, case_b)["AMOUNT"][0]
    unit = _ambiguous_unit(client, case_a)
    payload = _valid_payload(client, case_a, unit)
    payload["pairings"][0]["amount_fact_id"] = foreign["fact_id"]
    res = client.post(f"/api/v1/cases/{case_a}/reporting-units/{unit['unit_id']}/mapping", json=payload)
    assert res.status_code == 400


def test_mapping_rejects_malformed_pairing(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    grouped = _facts(client, case_id)
    dest = grouped["ACCOUNT"][0]
    res = client.post(
        f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping",
        json={"target_evidence_id": unit["evidence_ids"][0], "pairings": [{"destination_fact_id": dest["fact_id"]}]},
    )
    assert res.status_code == 400


def test_mapping_rejects_stale_version(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    # tab lain menyentuh satu fakta (versi naik, unit tetap ambigu)
    other = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"][0]
    bump = client.patch(
        f"/api/v1/cases/{case_id}/facts/{other['fact_id']}",
        json={"action": "confirm", "expected_version": version},
    )
    assert bump.status_code == 200
    assert client.get(f"/api/v1/cases/{case_id}").json()["version"] == version + 1
    same_unit = _ambiguous_unit(client, case_id)
    assert same_unit["unit_id"] == unit["unit_id"]
    # tab basi menyimpan dengan versi lama -> ditolak
    payload = _valid_payload(client, case_id, unit)
    payload["expected_version"] = version
    payload["idempotency_key"] = "stale-tab-key"
    second = client.post(f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping", json=payload)
    assert second.status_code == 409


def test_mapping_idempotent_same_key(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    payload = _valid_payload(client, case_id, unit)
    payload["expected_version"] = version
    payload["idempotency_key"] = "double-click-key"
    first = client.post(f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping", json=payload)
    assert first.status_code == 200
    second = client.post(f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping", json=payload)
    assert second.status_code == 200
    # double submit must not duplicate the decision nor bump the version twice
    units = client.get(f"/api/v1/cases/{case_id}/reporting-units").json()["reporting_units"]
    assert units == second.json()["reporting_units"]
    assert client.get(f"/api/v1/cases/{case_id}").json()["version"] == version + 1


def test_web_pairing_stale_redirects(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    page = client.get(f"/cases/{case_id}/review")
    assert page.status_code == 200
    grouped = _facts(client, case_id)
    dest = next(f for f in grouped["ACCOUNT"] if "DEST-A" in f["raw_value"])
    amt = next(f for f in grouped["AMOUNT"] if "2.000" in f["raw_value"])
    form = {
        "evidence_id": unit["evidence_ids"][0],
        "destination_fact_id_0": dest["fact_id"],
        "amount_fact_id_0": amt["fact_id"],
        "expected_version": -1,  # simulasi tab basi
        "idempotency_key": "web-stale-key",
    }
    res = client.post(f"/cases/{case_id}/pairing/{unit['unit_id']}", data=form, follow_redirects=False)
    assert res.status_code == 303
    assert "pairing-basi" in res.headers["location"]


def test_web_artifacts_page_renders_after_approval(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    payload = _valid_payload(client, case_id, unit)
    payload["expected_version"] = version
    mapped = client.post(f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping", json=payload)
    assert mapped.status_code == 200
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    ok = client.post(
        f"/api/v1/cases/{case_id}/approval",
        headers={"Idempotency-Key": "p0-web-art"},
        json={"snapshot_hash": draft.json()["snapshot_hash"], "accepted_notice": True},
    )
    assert ok.status_code == 200
    page = client.get(f"/cases/{case_id}/artifacts")
    assert page.status_code == 200
    assert "Paket Anda siap dibawa" in page.text
    assert "Buka situs IASC" in page.text


def test_dashboard_evidence_path_and_informed_bypass(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = _ambiguous_case(client, ocr)
    unit = _ambiguous_unit(client, case_id)
    grouped = _facts(client, case_id)
    dest_a = next(f for f in grouped["ACCOUNT"] if "DEST-A" in f["raw_value"])
    dest_b = next(f for f in grouped["ACCOUNT"] if "DEST-B" in f["raw_value"])
    amt_a = next(f for f in grouped["AMOUNT"] if "2.000" in f["raw_value"])
    amt_b = next(f for f in grouped["AMOUNT"] if "750" in f["raw_value"])
    time_a = next(f for f in grouped["DATETIME"] if "09:13" in f["raw_value"])
    time_b = next(f for f in grouped["DATETIME"] if "09:47" in f["raw_value"])
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    mapped = client.post(
        f"/api/v1/cases/{case_id}/reporting-units/{unit['unit_id']}/mapping",
        json={
            "target_evidence_id": unit["evidence_ids"][0],
            "expected_version": version,
            "pairings": [
                {"destination_fact_id": dest_a["fact_id"], "amount_fact_id": amt_a["fact_id"], "datetime_fact_id": time_a["fact_id"]},
                {"destination_fact_id": dest_b["fact_id"], "amount_fact_id": amt_b["fact_id"], "datetime_fact_id": time_b["fact_id"]},
            ],
        },
    )
    assert mapped.status_code == 200
    page = client.get(f"/cases/{case_id}/readiness")
    assert page.status_code == 200
    assert "Yang perlu Anda lakukan" in page.text
    assert "Tambah bukti" in page.text
    assert "Lanjut ke paket" in page.text
    import re

    visible = re.sub(r"<[^>]+>", " ", page.text)
    assert "READY" not in visible
    assert "ru_" not in visible
    assert "AMBIGUOUS" not in visible


def test_web_approval_double_submit_single_approval(client: TestClient, ocr: ScriptedOcr) -> None:
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "clean.png", CLEAN)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "p0-clean-run"})
    confirm_critical(client, case_id)
    draft = client.post(f"/api/v1/cases/{case_id}/draft")
    assert draft.status_code == 200
    snap = draft.json()["snapshot_hash"]
    page = client.get(f"/cases/{case_id}/approval")
    assert page.status_code == 200
    form = {"snapshot_hash": snap, "accepted_notice": "1", "idempotency_key": "web-double-key"}
    first = client.post(f"/cases/{case_id}/approval", data=form, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"].endswith("/artifacts")
    second = client.post(f"/cases/{case_id}/approval", data=form, follow_redirects=False)
    assert second.status_code == 303
    assert second.headers["location"].endswith("/artifacts")


def test_processing_get_does_not_enqueue(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    settings, _ocr, _container = tmp_env
    settings.sync_jobs = False
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "clean.png", CLEAN)
    before = client.get(f"/api/v1/cases/{case_id}").json()["state"]
    first = client.get(f"/cases/{case_id}/processing")
    second = client.get(f"/cases/{case_id}/processing")
    assert first.status_code == 200
    assert second.status_code == 200
    after = client.get(f"/api/v1/cases/{case_id}").json()["state"]
    assert before == after == "INGESTING"


def test_result_get_does_not_duplicate_jobs(client: TestClient, ocr: ScriptedOcr, tmp_env) -> None:
    settings, _ocr, _container = tmp_env
    case_id = create_case(client)
    upload_text_png(client, ocr, case_id, "clean.png", CLEAN)
    client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "p0-dedup-run"})
    confirm_critical(client, case_id)
    settings.sync_jobs = False  # matikan worker: kick hanya boleh enqueue
    first = client.get(f"/cases/{case_id}/readiness")
    assert first.status_code == 200
    from app.infrastructure.db import JobRow

    def _active(db) -> list:
        return [
            r
            for r in db.query(JobRow).filter(JobRow.case_id == case_id).all()
            if r.kind == "orchestrate" and r.status in {"pending", "running"}
        ]

    session_factory = _container.sessions
    with session_factory() as db:
        jobs = _active(db)
    assert len(jobs) == 1
    second = client.get(f"/cases/{case_id}/readiness")
    assert second.status_code == 200
    with session_factory() as db:
        jobs2 = _active(db)
    assert len(jobs2) == 1
