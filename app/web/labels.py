from __future__ import annotations

import re

_UNIT_RE = re.compile(r"\b(?:Unit\s+)?ru_[0-9a-f]+\b", re.I)
_ID_RE = re.compile(r"\b(?:ev|fact|conf|tx|act|art|case)-[0-9a-f]+\b", re.I)
_SPACE_RE = re.compile(r"\s+")
_JARGON = (
    ("AMBIGUOUS_MAPPING", "transaksi yang belum terpasang"),
    ("SHA-256", "kode cek keaslian"),
    ("Bank/PJP", "Bank"),
    ("bank/PJP", "bank"),
    (" atau PJP", ""),
    ("/PJP", ""),
    ("PJP", "bank"),
    ("nominal", "jumlah uang"),
    ("Nominal", "Jumlah uang"),
    ("kanal", "cara bayar"),
    ("Kanal", "Cara bayar"),
    ("Unggah", "Kirim"),
    ("unggah", "kirim"),
)

_TABLES: dict[str, dict[str, str]] = {
    "fact": {
        "PERSON_NAME": "Nama",
        "PHONE": "No. HP / telepon",
        "ACCOUNT": "Rekening penerima",
        "PJP": "Bank",
        "AMOUNT": "Jumlah uang (Rp)",
        "DATETIME": "Waktu",
        "CHANNEL": "Cara bayar",
        "URL": "Link",
        "CLAIM": "Keterangan",
        "EVENT": "Kejadian",
    },
    "review": {
        "CANDIDATE": "Perlu dicek",
        "CONFIRMED": "Benar",
        "CORRECTED": "Sudah dibetulkan",
        "REJECTED": "Bukan data saya",
        "UNAVAILABLE": "Belum ada",
    },
    "state": {
        "NEW": "Menyiapkan",
        "INGESTING": "Menerima bukti",
        "EXTRACTING": "Membaca bukti",
        "REVIEW_REQUIRED": "Siap dicek",
        "READY_FOR_ACTION": "Menyusun langkah",
        "WAITING_APPROVAL": "Menunggu persetujuan Anda",
        "GENERATING": "Membuat paket",
        "VERIFYING": "Memeriksa paket",
        "HANDOFF_READY": "Paket siap",
        "RECEIPT_RECORDED": "Nomor tercatat",
        "COMPLETE": "Selesai",
        "FAILED_SAFE": "Perlu isi manual",
        "PURGED": "Dihapus",
    },
    "tool": {
        "inspect_evidence": "Memeriksa file",
        "extract_candidate_facts": "Membaca bukti",
        "validate_case_facts": "Menyusun data",
        "assess_handoff_readiness": "Cek kelengkapan",
        "compile_action_plan": "Menyusun langkah",
        "compile_artifacts": "Menyiapkan paket",
        "verify_artifacts": "Memeriksa paket",
        "prepare_official_handoff": "Menyiapkan paket",
        "record_official_receipt": "Mencatat nomor",
        "purge_case": "Menghapus data",
        "resolve_unit_mapping": "Memasangkan transaksi",
    },
    "conflict": {
        "VALUE_MISMATCH": "Data berbeda",
        "TIME_ORDER": "Urutan waktu tidak cocok",
        "DUPLICATE": "Data ganda",
        "SOURCE_DISAGREEMENT": "Sumbernya beda-beda",
    },
    "channel": {
        "BANK_PJP": "Bank",
        "IASC": "IASC",
        "POLICE": "Polisi",
        "ACCOUNT_SECURITY": "Amankan akun",
        "MANUAL_VERIFY": "Cek sendiri",
        "READY": "Siap",
        "NEEDS_ACTION": "Perlu dilengkapi",
        "BLOCKED": "Belum bisa lanjut",
        "PASS": "Siap",
        "FAIL": "Belum lolos",
        "NOW": "Sekarang",
        "NEXT": "Berikutnya",
        "LATER": "Nanti",
    },
    "artifact": {
        "VERIFICATION_BRIEF": "Ringkasan hasil cek",
        "ACTION_PLAN": "Langkah lapor",
        "EVIDENCE_PACK": "Kumpulan bukti",
        "READINESS_REPORT": "Cek kelengkapan",
        "BANK_HANDOFF_PACK": "Untuk bank",
        "IASC_HANDOFF_PACK": "Untuk IASC",
        "POLICE_HANDOFF_PACK": "Untuk polisi",
        "REPORTING_UNIT_JSON": "Rincian transaksi",
        "UNIT_BANK_PACK": "Untuk bank",
        "UNIT_IASC_PACK": "Untuk IASC",
        "CASE_JSON": "Arsip data kasus",
        "CHECKLIST": "Daftar cek",
        "MANIFEST": "Kode cek keaslian",
        "CASE_ZIP": "Semua file (ZIP)",
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
