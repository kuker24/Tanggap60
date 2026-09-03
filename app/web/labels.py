from __future__ import annotations

import re

_UNIT_RE = re.compile(r"\b(?:Unit\s+)?ru_[0-9a-f]+\b", re.I)
_ID_RE = re.compile(r"\b(?:ev|fact|conf|tx|act|art|case)-[0-9a-f]+\b", re.I)
_SPACE_RE = re.compile(r"\s+")
_JARGON = (
    ("AMBIGUOUS_MAPPING", "pasangan yang belum jelas"),
    ("Bank/PJP", "Bank"),
    ("bank/PJP", "bank"),
    (" atau PJP", ""),
    ("/PJP", ""),
    ("PJP", "bank"),
)

_TABLES: dict[str, dict[str, str]] = {
    "fact": {
        "PERSON_NAME": "Nama",
        "PHONE": "Telepon",
        "ACCOUNT": "Rekening",
        "PJP": "Bank / PJP",
        "AMOUNT": "Nominal",
        "DATETIME": "Waktu",
        "CHANNEL": "Kanal",
        "URL": "Tautan",
        "CLAIM": "Klaim",
        "EVENT": "Kejadian",
    },
    "review": {
        "CANDIDATE": "Perlu dicek",
        "CONFIRMED": "Benar",
        "CORRECTED": "Diperbaiki",
        "REJECTED": "Bukan ini",
        "UNAVAILABLE": "Belum ada",
    },
    "state": {
        "NEW": "Menyiapkan",
        "INGESTING": "Menerima bukti",
        "EXTRACTING": "Membaca bukti",
        "REVIEW_REQUIRED": "Siap ditinjau",
        "READY_FOR_ACTION": "Menyusun langkah",
        "WAITING_APPROVAL": "Menunggu persetujuan",
        "GENERATING": "Membuat paket",
        "VERIFYING": "Memeriksa paket",
        "HANDOFF_READY": "Paket siap",
        "RECEIPT_RECORDED": "Tiket tercatat",
        "COMPLETE": "Selesai",
        "FAILED_SAFE": "Tidak bisa dilanjutkan otomatis",
        "PURGED": "Dihapus",
    },
    "tool": {
        "inspect_evidence": "Memeriksa berkas",
        "extract_candidate_facts": "Membaca bukti",
        "validate_case_facts": "Menyusun fakta",
        "assess_handoff_readiness": "Cek kelengkapan",
        "compile_action_plan": "Menyusun langkah",
        "compile_artifacts": "Menyiapkan paket",
        "verify_artifacts": "Memeriksa paket",
        "prepare_official_handoff": "Siap dibawa",
        "record_official_receipt": "Mencatat tiket",
        "purge_case": "Menghapus data",
        "resolve_unit_mapping": "Memasangkan transaksi",
    },
    "conflict": {
        "VALUE_MISMATCH": "Nilai berbeda",
        "TIME_ORDER": "Urutan waktu tidak cocok",
        "DUPLICATE": "Data ganda",
        "SOURCE_DISAGREEMENT": "Sumber tidak sepakat",
    },
    "channel": {
        "BANK_PJP": "Bank",
        "IASC": "IASC",
        "POLICE": "Polisi",
        "ACCOUNT_SECURITY": "Keamanan akun",
        "MANUAL_VERIFY": "Cek mandiri",
        "READY": "Siap",
        "NEEDS_ACTION": "Perlu dilengkapi",
        "BLOCKED": "Tertahan",
        "PASS": "Siap",
        "FAIL": "Gagal",
        "NOW": "Sekarang",
        "NEXT": "Berikutnya",
        "LATER": "Nanti",
    },
    "artifact": {
        "VERIFICATION_BRIEF": "Ringkasan cek",
        "ACTION_PLAN": "Rencana tindakan",
        "EVIDENCE_PACK": "Paket bukti",
        "READINESS_REPORT": "Cek kelengkapan",
        "BANK_HANDOFF_PACK": "Untuk bank",
        "IASC_HANDOFF_PACK": "Untuk IASC",
        "POLICE_HANDOFF_PACK": "Untuk polisi",
        "REPORTING_UNIT_JSON": "Data transaksi",
        "UNIT_BANK_PACK": "Untuk bank",
        "UNIT_IASC_PACK": "Untuk IASC",
        "CASE_JSON": "Data kasus",
        "CHECKLIST": "Daftar periksa",
        "MANIFEST": "Daftar isi",
        "CASE_ZIP": "Semua berkas",
    },
}


def human(value: object, kind: str = "generic") -> str:
    key = getattr(value, "value", value)
    if key is None:
        return ""
    text = str(key)
    return _TABLES.get(kind, {}).get(text, text.replace("_", " ").title())


def soften(value: object) -> str:
    text = "" if value is None else str(value)
    text = _UNIT_RE.sub("", text)
    text = _ID_RE.sub("", text)
    for src, dst in _JARGON:
        text = text.replace(src, dst)
    text = _SPACE_RE.sub(" ", text).strip(" —–-")
    if text:
        text = text[0].upper() + text[1:]
    return text
