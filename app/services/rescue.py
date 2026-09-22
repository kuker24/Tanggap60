from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models import ConflictRecord, EvidenceRecord, FactRecord, FactType, ReviewStatus

_DEVICE_TERMS = ("apk", "remote", "anydesk", "teamviewer", "aksesibilitas", "screen sharing")
_SIM_TERMS = ("sim", "nomor tidak aktif", "sinyal hilang", "ambil alih whatsapp", "whatsapp diambil")
_CREDENTIAL_TERMS = ("otp", "pin", "password", "kata sandi", "kode verifikasi", "credential")

BANK_HOTLINES: dict[str, dict[str, str]] = {
    "bca": {"name": "Bank Central Asia (BCA)", "hotline": "1500888", "tel": "1500888"},
    "mandiri": {"name": "Bank Mandiri", "hotline": "14000", "tel": "14000"},
    "bri": {"name": "Bank Rakyat Indonesia (BRI)", "hotline": "1500017", "tel": "1500017"},
    "bni": {"name": "Bank Negara Indonesia (BNI)", "hotline": "1500046", "tel": "1500046"},
    "cimb": {"name": "CIMB Niaga", "hotline": "14041", "tel": "14041"},
    "bsi": {"name": "Bank Syariah Indonesia (BSI)", "hotline": "14040", "tel": "14040"},
    "permata": {"name": "Bank Permata", "hotline": "1500111", "tel": "1500111"},
    "danamon": {"name": "Bank Danamon", "hotline": "1500090", "tel": "1500090"},
    "btn": {"name": "Bank BTN", "hotline": "1500286", "tel": "1500286"},
    "panin": {"name": "Bank Panin", "hotline": "1500678", "tel": "1500678"},
    "ocbc": {"name": "OCBC NISP", "hotline": "1500999", "tel": "1500999"},
    "jago": {"name": "Bank Jago", "hotline": "1500746", "tel": "1500746"},
    "jenius": {"name": "Jenius (BTPN)", "hotline": "1500365", "tel": "1500365"},
    "btpn": {"name": "Bank BTPN", "hotline": "1500365", "tel": "1500365"},
    "seabank": {"name": "SeaBank", "hotline": "1500130", "tel": "1500130"},
    "allo": {"name": "Allo Bank", "hotline": "02130001600", "tel": "02130001600"},
    "blu": {"name": "blu by BCA Digital", "hotline": "1500668", "tel": "1500668"},
    "gopay": {"name": "GoPay", "hotline": "1500304", "tel": "1500304"},
    "ovo": {"name": "OVO", "hotline": "1500696", "tel": "1500696"},
    "dana": {"name": "DANA", "hotline": "1500445", "tel": "1500445"},
    "shopeepay": {"name": "ShopeePay", "hotline": "02139500300", "tel": "02139500300"},
}


def resolve_bank_hotline(bank_name_raw: str | None) -> dict[str, str]:
    if not bank_name_raw:
        return {
            "name": "Bank / Lembaga Terkait",
            "hotline": "Call Center Bank / Kontak OJK 157",
            "tel": "157",
        }
    raw_lower = bank_name_raw.lower()
    for key, info in BANK_HOTLINES.items():
        if key in raw_lower:
            return info
    return {
        "name": bank_name_raw,
        "hotline": f"Call Center {bank_name_raw}",
        "tel": "157",
    }


def _format_amount(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, int | float):
        try:
            return f"Rp {int(val):,}".replace(",", ".")
        except Exception:
            return str(val)
    raw = str(val).strip()
    if "rp" in raw.lower():
        return raw
    digits = "".join(c for c in raw if c.isdigit())
    if digits:
        try:
            return f"Rp {int(digits):,}".replace(",", ".")
        except Exception:
            pass
    return raw


def _format_time(raw: Any) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %H:%M WIB")
    except Exception:
        return s


def build_bank_call_script(
    facts: list[FactRecord],
    units: list[Any] | None = None,
) -> dict[str, Any]:
    """Menyusun skrip panggilan darurat 30 detik untuk dibacakan korban ke CS Bank saat meminta pembekuan rekening penampung."""
    bank_name: str | None = None
    account_no: str | None = None
    person_name: str | None = None
    amount_str: str | None = None
    time_str: str | None = None

    if units:
        for u in units:
            dest = getattr(u, "destination_account", None)
            if dest and not account_no:
                account_no = str(dest)
            amt = getattr(u, "amount", None)
            if amt and not amount_str:
                amount_str = _format_amount(amt)
            tx_time = getattr(u, "transferred_at", None)
            if tx_time and not time_str:
                time_str = _format_time(tx_time)

    confirmed_facts = [f for f in facts if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}]
    search_facts = confirmed_facts if confirmed_facts else [f for f in facts if f.review_status == ReviewStatus.CANDIDATE]

    for f in search_facts:
        val = (f.normalized_value or f.raw_value or "").strip()
        if not val or "VICTIM" in (f.raw_value or ""):
            continue
        if f.type == FactType.PJP and not bank_name:
            bank_name = val
        elif f.type == FactType.ACCOUNT and not account_no:
            account_no = val
        elif f.type == FactType.PERSON_NAME and not person_name:
            person_name = val
        elif f.type == FactType.AMOUNT and not amount_str:
            amount_str = _format_amount(val)
        elif f.type == FactType.DATETIME and not time_str:
            time_str = _format_time(val)

    has_data = bool(account_no or bank_name or amount_str)
    hotline_info = resolve_bank_hotline(bank_name)
    display_bank = hotline_info["name"]
    display_account = account_no or "Perlu dikonfirmasi"
    display_name = person_name or "Sesuai data bank tujuan"
    display_amount = amount_str or "Sesuai bukti transfer"
    display_time = time_str or "Baru saja terjadi"

    script_text = (
        f"Halo Customer Service {display_bank}, saya korban penipuan transfer perbankan yang baru saja terjadi. "
        f"Mohon bantu lakukan pembekuan sementara (temporary freeze) pada rekening penampung tujuan berikut secepatnya:\n\n"
        f"• Nomor Rekening Tujuan: {display_account}\n"
        f"• Atas Nama Penerima: {display_name}\n"
        f"• Nominal Kerugian: {display_amount}\n"
        f"• Waktu Transaksi: {display_time}\n\n"
        f"Bukti transfer dan rekapitulasi data telah saya susun untuk verifikasi laporan resmi."
    )

    return {
        "has_data": has_data,
        "bank_name": display_bank,
        "hotline": hotline_info["hotline"],
        "hotline_tel": hotline_info["tel"],
        "destination_account": display_account,
        "destination_name": display_name,
        "amount": display_amount,
        "time": display_time,
        "script_text": script_text,
    }


def _reviewed_text(facts: list[FactRecord]) -> str:
    return " ".join(
        fact.raw_value.lower()
        for fact in facts
        if fact.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}
    )


def _latest_incident_time(facts: list[FactRecord]) -> datetime | None:
    parsed: list[datetime] = []
    for fact in facts:
        if fact.type != FactType.DATETIME or fact.review_status == ReviewStatus.REJECTED:
            continue
        raw = str(fact.normalized_value or fact.raw_value).strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        parsed.append(value.astimezone(UTC))
    return max(parsed, default=None)


def build_golden_window(
    *,
    facts: list[FactRecord],
    evidence: list[EvidenceRecord],
    conflicts: list[ConflictRecord],
    next_action: dict[str, Any] | None,
    now: datetime | None = None,
    units: list[Any] | None = None,
) -> dict[str, Any]:
    """Build guidance-only rescue state from reviewed facts; never executes an external action."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    incident_at = _latest_incident_time(facts)
    elapsed_minutes = None
    if incident_at and incident_at <= current:
        elapsed_minutes = max(0, int((current - incident_at).total_seconds() // 60))

    bank_call = build_bank_call_script(facts=facts, units=units)

    text = _reviewed_text(facts)
    has_device = any(term in text for term in _DEVICE_TERMS)
    has_sim = any(term in text for term in _SIM_TERMS)
    has_credentials = any(term in text for term in _CREDENTIAL_TERMS)
    has_transfer = any(
        fact.type == FactType.AMOUNT and fact.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}
        for fact in facts
    )

    if has_device:
        scope = "Perangkat mungkin terpapar"
        action = "Simpan bukti penting, lalu hentikan koneksi perangkat yang dicurigai"
        reason = "Fakta yang ditinjau menyebut aplikasi atau akses jarak jauh. Amankan bukti sebelum mengubah atau mereset perangkat."
        channel = "Perangkat dan akun resmi"
    elif has_sim:
        scope = "Nomor atau akun komunikasi mungkin diambil alih"
        action = "Hubungi operator lewat kanal resmi dari perangkat lain"
        reason = "Gangguan SIM atau pengambilalihan akun dapat memengaruhi pemulihan akun lain."
        channel = "Operator seluler resmi"
    elif has_credentials:
        scope = "Kredensial mungkin terpapar"
        action = "Amankan akun melalui aplikasi atau situs resmi"
        reason = "Fakta yang ditinjau menyebut kode atau kredensial. Jangan memakai tautan maupun kontak dari percakapan."
        channel = "Aplikasi atau situs resmi"
    elif has_transfer:
        scope = "Dana sudah berpindah"
        action = str((next_action or {}).get("label") or "Hubungi bank atau PJP lewat kanal resmi")
        reason = str(
            (next_action or {}).get("reason")
            or "Data transaksi ditemukan. Tindakan melalui kanal resmi biasanya lebih bernilai daripada melanjutkan percakapan."
        )
        channel = "Bank atau PJP resmi"
    else:
        scope = "Cakupan belum cukup jelas"
        action = "Bekukan keputusan dan simpan bukti yang masih terlihat"
        reason = "Belum ada fakta terkonfirmasi yang cukup untuk memberi tindakan spesifik."
        channel = "Belum ditentukan"

    if elapsed_minutes is None:
        band = "Waktu belum terkonfirmasi"
        timing = "Konfirmasi waktu kejadian agar urutan tindakan lebih tepat."
    elif elapsed_minutes <= 60:
        band = "Bertindak sekarang"
        timing = f"Kejadian terkonfirmasi sekitar {elapsed_minutes} menit lalu. Ini panduan prioritas, bukan jaminan pemulihan."
    elif elapsed_minutes <= 24 * 60:
        band = "Masih bernilai"
        timing = "Tindakan resmi dan pengamanan bukti masih perlu dilakukan tanpa menunda."
    else:
        band = "Amankan dan lanjutkan"
        timing = "Jangan abaikan kasus karena waktu berlalu; pertahankan bukti dan lanjutkan ke kanal resmi."

    checklist = [
        "Jangan transfer lagi, membagikan OTP/PIN, atau mengikuti tautan dari percakapan.",
        "Simpan screenshot, bukti transaksi, alamat akun, dan waktu sebelum menghapus atau mereset apa pun.",
        f"Gunakan {channel.lower()}, bukan nomor atau tautan yang diberikan pihak terduga.",
    ]
    if any(c.status.value == "OPEN" for c in conflicts):
        checklist.append("Biarkan data yang bertentangan tetap terlihat sampai Anda memilih sumber yang benar.")

    return {
        "band": band,
        "timing": timing,
        "scope": scope,
        "action": action,
        "reason": reason,
        "checklist": checklist,
        "evidence_count": len(evidence),
        "guidance_only": True,
        "bank_call": bank_call,
    }


def _mid_sentence(text: str) -> str:
    """Lowercase the leading letter for mid-sentence use, but leave an all-caps
    token (e.g. AMBIGUOUS_MAPPING) intact so labels.soften() can still map it."""
    if not text:
        return text
    first = text.split(" ", 1)[0]
    if first.isupper() or "_" in first:
        return text
    return text[:1].lower() + text[1:]


def build_adversarial_checks(units_report: dict[str, Any] | None, *, limit: int = 3) -> dict[str, Any]:
    """Turn readiness gaps into likely intake questions without claiming official rejection."""
    candidates: list[dict[str, Any]] = []
    report = units_report or {}
    groups = [
        (str(unit.get("unit_id") or "transaksi"), unit.get("channels") or [])
        for unit in report.get("units") or []
    ]
    police = report.get("incident_police") or {}
    if police:
        groups.append(("insiden", [police]))

    seen: set[str] = set()
    for target, channels in groups:
        for channel in channels:
            channel_label = str(channel.get("label") or channel.get("channel") or "kanal")
            for check in channel.get("checks") or []:
                if check.get("status") not in {"MISSING", "CONFLICT"}:
                    continue
                check_id = str(check.get("check_id") or "")
                if check_id in seen:
                    continue
                seen.add(check_id)
                candidates.append(
                    {
                        "check_id": check_id,
                        "channel": channel_label,
                        "target": target,
                        "question": str(check.get("action") or check.get("label") or "Lengkapi data pendukung"),
                        "challenge": f"{channel_label} mungkin meminta klarifikasi karena {_mid_sentence(str(check.get('reason') or 'data belum lengkap'))}.",
                        "fact_ids": list(check.get("fact_ids") or []),
                        "evidence_ids": list(check.get("evidence_ids") or []),
                        "blocking": bool(check.get("blocking")),
                    }
                )

    candidates.sort(key=lambda row: (not row["blocking"], row["channel"], row["check_id"]))
    return {
        "findings": candidates[:limit],
        "remaining_count": max(0, len(candidates) - limit),
        "passed": not candidates,
        "disclaimer": "Simulasi kesiapan internal Tanggap60, bukan keputusan atau jaminan penerimaan lembaga.",
    }
