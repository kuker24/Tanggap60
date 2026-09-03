"""Safe Workspace: derivasi read-only dari fakta CONFIRMED.

Bukan portal pemerintah palsu. Field yang belum diketahui TIDAK diisi
("Belum tersedia") — tidak ada hallucination. Identitas korban selalu
tetap milik pengguna (tidak diisi AI).
"""

from __future__ import annotations

from typing import Any

from app.agent.broker import WORKSPACE_FIELDS
from app.agent.context import build_agent_context
from app.agent.formatting import mask_account
from app.infrastructure.repositories import EvidenceRepository, FactRepository

NOT_AVAILABLE = "Belum tersedia — isi sendiri"

UNCONFIRMED = "Belum tersedia — perlu dikonfirmasi dulu"


def prepare_workspace(db: Any, case_id: str, mask_destination: bool = False) -> dict[str, Any]:
    """Siapkan field workspace. Murni derivasi; tanpa penyimpanan.
    
    mask_destination=False: hanya untuk sesi pelaporan resmi korban di browser
    miliknya (UI menampilkan nomor rekening lengkap agar bisa dicopy ke form resmi).
    Agent context, audit log, dan telemetry tetap masked.
    """
    context = build_agent_context(db, case_id)
    facts = {f.fact_id: f for f in FactRepository(db).list_for_case(case_id)}
    evidence_names = {e.evidence_id: e.original_name_display for e in EvidenceRepository(db).list_for_case(case_id)}

    complete = [u for u in context["units"] if u["mapping_status"] in {"COMPLETE", "INCOMPLETE"}]
    transactions: list[dict[str, Any]] = []
    for unit in complete:
        dest_name: str | None = None
        dest_bank: str | None = None
        for fid in unit["fact_ids"]:
            fact = facts.get(fid)
            if fact is None:
                continue
            ftype = fact.type.value if hasattr(fact.type, "value") else str(fact.type)
            if ftype == "PERSON_NAME" and fact.review_status.value in {"CONFIRMED", "CORRECTED"}:
                dest_name = fact.raw_value
            if ftype == "PJP" and fact.review_status.value in {"CONFIRMED", "CORRECTED"}:
                dest_bank = fact.normalized_value or fact.raw_value
        when = unit["transferred_at"] or ""
        date, _, time = when.partition(" ")
        dest = _dest_raw(facts, unit, mask=mask_destination)
        amount = unit["amount_text"] if unit.get("amount") is not None else UNCONFIRMED
        transactions.append(
            {
                "destination_account": dest if dest else UNCONFIRMED,
                "destination_bank": dest_bank or NOT_AVAILABLE,
                "destination_name": dest_name or NOT_AVAILABLE,
                "amount": amount,
                "date": date or UNCONFIRMED,
                "time": time or UNCONFIRMED,
                "unit_id": unit["unit_id"],
            }
        )

    chronology: list[str] = []
    for index, tx in enumerate(transactions, start=1):
        if tx["amount"] == UNCONFIRMED:
            continue
        when = f"pada {tx['date']} {tx['time']}".strip() if tx["date"] != UNCONFIRMED else "(waktu perlu dikonfirmasi)"
        chronology.append(f"{index}. Transfer {tx['amount']} ke rekening {tx['destination_account']} {when}.".strip())

    evidence_refs = sorted({evidence_names.get(eid, eid) for u in complete for eid in u["evidence_ids"]})
    confirmed_count = sum(1 for t in transactions if t["amount"] != UNCONFIRMED and t["destination_account"] != UNCONFIRMED)
    checklist = [
        {"item": "Transaksi terkonfirmasi", "done": confirmed_count > 0, "detail": f"{confirmed_count} transaksi"},
        {"item": "Nominal jelas", "done": all(t["amount"] != UNCONFIRMED for t in transactions), "detail": ""},
        {"item": "Waktu jelas", "done": all(t["date"] != UNCONFIRMED for t in transactions), "detail": ""},
        {"item": "Identitas korban", "done": False, "detail": "Hanya Anda yang mengisi — AI tidak menyentuh data ini"},
        {"item": "Kirim ke portal resmi", "done": False, "detail": "Anda lakukan sendiri — AI berhenti di sini"},
    ]

    fields: dict[str, Any] = {
        "victim_account": NOT_AVAILABLE,
        "victim_bank": NOT_AVAILABLE,
        "transactions": transactions,
        "chronology": chronology,
        "evidence_refs": evidence_refs,
        "checklist": checklist,
    }
    assert set(fields) <= WORKSPACE_FIELDS | {"transactions"}
    log = [
        "membuka workspace persiapan",
        f"menyiapkan {len(transactions)} transaksi teridentifikasi",
        "menyusun kronologi dari fakta yang ditinjau",
        "identitas korban dibiarkan kosong untuk diisi pengguna sendiri",
    ]
    return {
        "simulation": True,
        "simulation_label": "SIMULASI PERSIAPAN FORM — BUKAN PORTAL RESMI",
        "fields": fields,
        "action_log": log,
        "confirmed_transactions": confirmed_count,
        "official_note": "Dokumen belum dikirim ke mana pun. Pengiriman tetap Anda lakukan sendiri di portal resmi.",
    }


def _dest_raw(facts: dict[str, Any], unit: dict[str, Any], mask: bool = False) -> str | None:
    for fid in unit["fact_ids"]:
        fact = facts.get(fid)
        if fact is None:
            continue
        ftype = fact.type.value if hasattr(fact.type, "value") else str(fact.type)
        if ftype == "ACCOUNT":
            val = fact.normalized_value or fact.raw_value
            return mask_account(val) if mask else str(val)
    masked = unit.get("destination_masked")
    return str(masked) if masked else None
