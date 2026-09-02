from __future__ import annotations

from app.domain.states import TOOLS_BY_STATE, State

TOOL_SPECS: tuple[dict[str, str], ...] = (
    {
        "name": "inspect_evidence",
        "description": "Baca bukti, OCR/PDF, simpan teks turunan beserta locator halaman.",
    },
    {
        "name": "extract_candidate_facts",
        "description": "Ekstrak kandidat fakta. Model opsional, regex memverifikasi angka/rekening.",
    },
    {
        "name": "validate_case_facts",
        "description": "Deteksi konflik, rute, dan kesiapan tinjauan manusia.",
    },
    {
        "name": "build_preincident_brief",
        "description": "Susun Verification Brief CekDulu tanpa fetch URL.",
    },
    {
        "name": "build_postincident_plan",
        "description": "Susun Emergency Action Plan pascainsiden.",
    },
    {
        "name": "compile_artifacts",
        "description": "Render PDF/JSON/ZIP dari snapshot persetujuan.",
    },
    {
        "name": "verify_artifacts",
        "description": "Verifikasi schema, hash, halaman PDF, dan SHA di manifest ZIP.",
    },
    {
        "name": "prepare_official_handoff",
        "description": "Siapkan URL IASC allowlist. Tidak mengirim laporan.",
    },
    {
        "name": "record_handoff_receipt",
        "description": "Catat nomor tiket lokal. Official status selalu NOT_VERIFIED.",
    },
    {
        "name": "purge_case",
        "description": "Hapus bukti dan data kasus atas permintaan pengguna.",
    },
)


def allowed_tools(state: str) -> tuple[str, ...]:
    try:
        return TOOLS_BY_STATE.get(State(state), ())
    except ValueError:
        return ()


def tool_names() -> tuple[str, ...]:
    return tuple(spec["name"] for spec in TOOL_SPECS)
