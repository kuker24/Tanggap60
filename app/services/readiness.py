from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.errors import ValidationFailed
from app.domain.models import (
    REVIEWED_FACT_STATUSES,
    ConflictRecord,
    EvidenceRecord,
    EvidenceStatus,
    FactRecord,
    FactType,
    ReviewStatus,
    TransactionGroupRecord,
)
from app.domain.policies import blocking_conflicts_open, canonical_json
from app.domain.states import Route

PROFILE_PATH = Path(__file__).resolve().parents[1] / "data" / "readiness_profiles.json"
CHANNELS = ("BANK_PJP", "IASC", "POLICE")
LEVELS = frozenset({"REQUIRED", "RECOMMENDED", "PREPARE_EXTERNALLY"})
CHECK_STATUSES = frozenset({"MET", "MISSING", "CONFLICT", "PREPARE_EXTERNALLY"})
KNOWN_CHECKS = frozenset(
    {
        "BANK_AMOUNT_REVIEWED",
        "BANK_DESTINATION_REVIEWED",
        "BANK_TIME_REVIEWED",
        "BANK_TRANSFER_EVIDENCE",
        "BANK_CHANNEL_OR_PJP",
        "BANK_IDENTITY_EXTERNAL",
        "IASC_AMOUNT_REVIEWED",
        "IASC_DESTINATION_REVIEWED",
        "IASC_TIME_REVIEWED",
        "IASC_TRANSFER_EVIDENCE",
        "IASC_COMMUNICATION_EVIDENCE",
        "IASC_CHRONOLOGY",
        "IASC_VICTIM_DATA_EXTERNAL",
        "POLICE_CHRONOLOGY",
        "POLICE_TRANSACTION",
        "POLICE_COMMUNICATION",
        "POLICE_EVIDENCE_INDEX",
        "POLICE_PROVENANCE",
        "POLICE_IDENTITY_EXTERNAL",
        "GLOBAL_NO_BLOCKING_CONFLICT",
        "GLOBAL_CRITICAL_REVIEWED",
        "GLOBAL_POST_INCIDENT_ROUTE",
    }
)
STATUS_LABELS = {
    "READY": "Siap dibuatkan draf",
    "NEEDS_ACTION": "Masih perlu diperbaiki",
    "BLOCKED": "Terblokir oleh konflik bukti",
}
EXTERNAL_LABEL = "Siapkan langsung di situs resmi"

GLOBAL_CHECKS = (
    {
        "check_id": "GLOBAL_NO_BLOCKING_CONFLICT",
        "label": "Tidak ada konflik bukti yang masih terbuka",
        "level": "REQUIRED",
    },
    {
        "check_id": "GLOBAL_CRITICAL_REVIEWED",
        "label": "Fakta kritis sudah ditinjau",
        "level": "REQUIRED",
    },
    {
        "check_id": "GLOBAL_POST_INCIDENT_ROUTE",
        "label": "Kasus adalah alur pascainsiden",
        "level": "REQUIRED",
    },
)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    label: str
    status: str
    reason: str
    fact_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    action: str
    blocking: bool
    level: str

    def as_public(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "label": self.label,
            "status": self.status,
            "reason": self.reason,
            "fact_ids": list(self.fact_ids),
            "evidence_ids": list(self.evidence_ids),
            "action": self.action,
            "blocking": self.blocking,
            "level": self.level,
        }

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "blocking": self.blocking,
        }


def load_profile(path: Path | None = None) -> dict[str, Any]:
    target = path or PROFILE_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationFailed("profil kesiapan tidak dapat dibaca") from exc
    return validate_profile(raw)


def validate_profile(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationFailed("profil kesiapan tidak valid")
    version = str(raw.get("profile_version") or "")
    if not version:
        raise ValidationFailed("profil kesiapan tanpa versi")
    if not str(raw.get("disclaimer") or ""):
        raise ValidationFailed("profil kesiapan tanpa disclaimer")
    if not str(raw.get("last_reviewed_at") or ""):
        raise ValidationFailed("profil kesiapan tanpa tanggal peninjauan")
    sources = raw.get("source_urls") or raw.get("source_url")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list) or not sources:
        raise ValidationFailed("profil kesiapan tanpa sumber publik")
    channels = raw.get("channels")
    if not isinstance(channels, dict) or set(channels) != set(CHANNELS):
        raise ValidationFailed("profil kesiapan kanal tidak lengkap")
    seen: set[str] = set()
    for name in CHANNELS:
        block = channels[name]
        checks = block.get("checks") if isinstance(block, dict) else None
        if not isinstance(checks, list) or not checks:
            raise ValidationFailed(f"profil kanal {name} tanpa checks")
        for item in checks:
            if not isinstance(item, dict):
                raise ValidationFailed("check profil tidak valid")
            cid = str(item.get("check_id") or "")
            level = str(item.get("level") or "")
            if cid in seen or cid not in KNOWN_CHECKS:
                raise ValidationFailed("check_id profil tidak sah")
            if level not in LEVELS:
                raise ValidationFailed("level check tidak sah")
            if not str(item.get("label") or ""):
                raise ValidationFailed("label check kosong")
            seen.add(cid)
    return {
        "profile_version": version,
        "last_reviewed_at": str(raw["last_reviewed_at"]),
        "source_urls": [str(u) for u in sources],
        "disclaimer": str(raw["disclaimer"]),
        "channels": channels,
    }


def _usable_facts(facts: list[FactRecord]) -> list[FactRecord]:
    return [f for f in facts if f.review_status != ReviewStatus.REJECTED]


def _reviewed_of(facts: list[FactRecord], types: set[FactType]) -> list[FactRecord]:
    return [
        f
        for f in facts
        if f.type in types and f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}
    ]


def _unavailable_of(facts: list[FactRecord], types: set[FactType]) -> list[FactRecord]:
    return [f for f in facts if f.type in types and f.review_status == ReviewStatus.UNAVAILABLE]


def _named_evidence(evidence: list[EvidenceRecord], needles: tuple[str, ...]) -> list[EvidenceRecord]:
    found: list[EvidenceRecord] = []
    for item in evidence:
        if item.status in {EvidenceStatus.REJECTED, EvidenceStatus.PURGED}:
            continue
        name = item.original_name_display.lower()
        if any(needle in name for needle in needles):
            found.append(item)
    return found


def _live_evidence(evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
    return [e for e in evidence if e.status not in {EvidenceStatus.REJECTED, EvidenceStatus.PURGED}]


def _result(
    spec: dict[str, str],
    status: str,
    reason: str,
    *,
    facts: list[FactRecord] | tuple[FactRecord, ...] = (),
    evidence: list[EvidenceRecord] | tuple[EvidenceRecord, ...] = (),
    action: str = "",
    blocking: bool | None = None,
) -> CheckResult:
    level = spec["level"]
    if status == "PREPARE_EXTERNALLY":
        block = False
    elif blocking is None:
        block = level == "REQUIRED" and status in {"MISSING", "CONFLICT"}
    else:
        block = blocking
    return CheckResult(
        check_id=spec["check_id"],
        label=spec["label"],
        status=status,
        reason=reason,
        fact_ids=tuple(f.fact_id for f in facts),
        evidence_ids=tuple(e.evidence_id for e in evidence),
        action=action,
        blocking=block,
        level=level,
    )


def _external(spec: dict[str, str]) -> CheckResult:
    return _result(
        spec,
        "PREPARE_EXTERNALLY",
        EXTERNAL_LABEL,
        action=EXTERNAL_LABEL,
        blocking=False,
    )


def _fact_check(
    spec: dict[str, str],
    facts: list[FactRecord],
    types: set[FactType],
    missing_action: str,
    *,
    allow_unavailable: bool = False,
) -> CheckResult:
    reviewed = _reviewed_of(facts, types)
    if reviewed:
        return _result(spec, "MET", "Fakta sudah ditinjau", facts=reviewed)
    unavailable = _unavailable_of(facts, types)
    if allow_unavailable and unavailable:
        return _result(spec, "MET", "Pengguna menandai data tidak tersedia", facts=unavailable)
    return _result(spec, "MISSING", "Belum ada fakta ditinjau", action=missing_action)


def _evidence_check(
    spec: dict[str, str],
    items: list[EvidenceRecord],
    missing_action: str,
    *,
    unavailable_facts: list[FactRecord] | None = None,
) -> CheckResult:
    if items:
        return _result(spec, "MET", "Bukti tersedia", evidence=items)
    if unavailable_facts:
        return _result(spec, "MET", "Pengguna menandai bukti tidak tersedia", facts=unavailable_facts)
    return _result(spec, "MISSING", "Bukti belum ada", action=missing_action)


def _channel_status(checks: list[CheckResult]) -> str:
    if any(item.check_id == "GLOBAL_POST_INCIDENT_ROUTE" and item.status != "MET" for item in checks):
        return "BLOCKED"
    if any(item.status == "CONFLICT" and item.blocking for item in checks):
        return "BLOCKED"
    if any(item.status == "MISSING" and item.blocking for item in checks):
        return "NEEDS_ACTION"
    if any(item.level == "REQUIRED" and item.status == "MISSING" for item in checks):
        return "NEEDS_ACTION"
    return "READY"


def _global_checks(
    *,
    route: Route,
    facts: list[FactRecord],
    conflicts: list[ConflictRecord],
) -> list[CheckResult]:
    open_blocking = blocking_conflicts_open(conflicts)
    conflict_spec = GLOBAL_CHECKS[0]
    if open_blocking:
        conflict = _result(
            conflict_spec,
            "CONFLICT",
            "Masih ada konflik bukti yang harus dipilih pengguna",
            action="Selesaikan konflik di tinjauan fakta",
            blocking=True,
        )
    else:
        conflict = _result(conflict_spec, "MET", "Tidak ada konflik wajib yang terbuka")
    critical = [
        f
        for f in facts
        if f.criticality.value == "CRITICAL" and f.review_status != ReviewStatus.REJECTED
    ]
    crit_spec = GLOBAL_CHECKS[1]
    if not critical or all(f.review_status in REVIEWED_FACT_STATUSES for f in critical):
        critical_check = _result(crit_spec, "MET", "Fakta kritis sudah ditinjau", facts=critical)
    else:
        pending = [f for f in critical if f.review_status not in REVIEWED_FACT_STATUSES]
        critical_check = _result(
            crit_spec,
            "MISSING",
            "Masih ada fakta kritis yang belum ditinjau",
            facts=pending,
            action="Tinjau fakta kritis",
            blocking=True,
        )
    route_spec = GLOBAL_CHECKS[2]
    if route == Route.POST_INCIDENT_RESPONSE:
        route_check = _result(route_spec, "MET", "Alur pascainsiden")
    else:
        route_check = _result(
            route_spec,
            "MISSING",
            "Paket kanal hanya untuk kasus pascainsiden",
            action="Gunakan CekDulu untuk pemeriksaan sebelum rugi",
            blocking=True,
        )
    return [conflict, critical_check, route_check]


def _bank_checks(profile: dict[str, Any], facts: list[FactRecord], evidence: list[EvidenceRecord]) -> list[CheckResult]:
    specs = {item["check_id"]: item for item in profile["channels"]["BANK_PJP"]["checks"]}
    transfer = _named_evidence(evidence, ("transfer", "bukti tf", "struk"))
    if not transfer:
        transfer = [
            e
            for e in _live_evidence(evidence)
            if any(f.source_evidence_id == e.evidence_id for f in _reviewed_of(facts, {FactType.AMOUNT}))
        ]
    return [
        _fact_check(specs["BANK_AMOUNT_REVIEWED"], facts, {FactType.AMOUNT}, "Tinjau nominal di halaman fakta"),
        _fact_check(
            specs["BANK_DESTINATION_REVIEWED"],
            facts,
            {FactType.ACCOUNT, FactType.PJP},
            "Tinjau rekening atau PJP tujuan",
        ),
        _fact_check(specs["BANK_TIME_REVIEWED"], facts, {FactType.DATETIME}, "Tinjau waktu transaksi"),
        _evidence_check(specs["BANK_TRANSFER_EVIDENCE"], transfer, "Unggah bukti transfer"),
        _fact_check(
            specs["BANK_CHANNEL_OR_PJP"],
            facts,
            {FactType.CHANNEL, FactType.PJP},
            "Tambahkan kanal atau nama PJP bila diketahui",
        ),
        _external(specs["BANK_IDENTITY_EXTERNAL"]),
    ]


def _iasc_checks(profile: dict[str, Any], facts: list[FactRecord], evidence: list[EvidenceRecord]) -> list[CheckResult]:
    specs = {item["check_id"]: item for item in profile["channels"]["IASC"]["checks"]}
    transfer = _named_evidence(evidence, ("transfer", "bukti tf", "struk"))
    chat = _named_evidence(evidence, ("chat", "pesan", "wa", "whatsapp"))
    unavailable_comm = _unavailable_of(facts, {FactType.CLAIM, FactType.CHANNEL})
    chronology_facts = _reviewed_of(facts, {FactType.DATETIME, FactType.EVENT, FactType.CLAIM})
    return [
        _fact_check(specs["IASC_AMOUNT_REVIEWED"], facts, {FactType.AMOUNT}, "Tinjau nominal"),
        _fact_check(specs["IASC_DESTINATION_REVIEWED"], facts, {FactType.ACCOUNT, FactType.PJP}, "Tinjau rekening tujuan"),
        _fact_check(specs["IASC_TIME_REVIEWED"], facts, {FactType.DATETIME}, "Tinjau waktu transaksi"),
        _evidence_check(specs["IASC_TRANSFER_EVIDENCE"], transfer, "Unggah bukti transfer"),
        _evidence_check(
            specs["IASC_COMMUNICATION_EVIDENCE"],
            chat,
            "Unggah bukti chat atau tandai tidak tersedia",
            unavailable_facts=unavailable_comm,
        ),
        _result(
            specs["IASC_CHRONOLOGY"],
            "MET" if chronology_facts else "MISSING",
            "Kronologi tersusun dari fakta yang ditinjau" if chronology_facts else "Belum ada rangkaian waktu/kejadian",
            facts=chronology_facts,
            action="" if chronology_facts else "Tinjau waktu atau klaim kejadian",
        ),
        _external(specs["IASC_VICTIM_DATA_EXTERNAL"]),
    ]


def _police_checks(
    profile: dict[str, Any],
    facts: list[FactRecord],
    evidence: list[EvidenceRecord],
    transactions: list[TransactionGroupRecord],
) -> list[CheckResult]:
    specs = {item["check_id"]: item for item in profile["channels"]["POLICE"]["checks"]}
    chronology_facts = _reviewed_of(facts, {FactType.DATETIME, FactType.EVENT, FactType.CLAIM})
    tx_facts = _reviewed_of(facts, {FactType.AMOUNT, FactType.ACCOUNT, FactType.DATETIME})
    chat = _named_evidence(evidence, ("chat", "pesan", "wa", "whatsapp"))
    unavailable_comm = _unavailable_of(facts, {FactType.CLAIM, FactType.CHANNEL})
    live = _live_evidence(evidence)
    reviewed = [f for f in facts if f.review_status in REVIEWED_FACT_STATUSES]
    provenance_ok = bool(reviewed) and all(
        f.source_evidence_id and (f.source_bbox or f.source_excerpt_hash) for f in reviewed
    )
    return [
        _result(
            specs["POLICE_CHRONOLOGY"],
            "MET" if chronology_facts else "MISSING",
            "Kronologi tersedia" if chronology_facts else "Kronologi belum lengkap",
            facts=chronology_facts,
            action="" if chronology_facts else "Tinjau waktu dan urutan kejadian",
        ),
        _result(
            specs["POLICE_TRANSACTION"],
            "MET" if tx_facts or transactions else "MISSING",
            "Transaksi tersedia" if tx_facts or transactions else "Data transaksi belum ditinjau",
            facts=tx_facts,
            action="" if tx_facts or transactions else "Tinjau nominal, rekening, dan waktu",
        ),
        _evidence_check(
            specs["POLICE_COMMUNICATION"],
            chat,
            "Unggah bukti komunikasi atau tandai tidak tersedia",
            unavailable_facts=unavailable_comm,
        ),
        _evidence_check(specs["POLICE_EVIDENCE_INDEX"], live, "Unggah paling tidak satu bukti"),
        _result(
            specs["POLICE_PROVENANCE"],
            "MET" if provenance_ok else "MISSING",
            "Setiap fakta ditinjau punya sumber" if provenance_ok else "Ada fakta tanpa sumber bukti",
            facts=reviewed,
            action="" if provenance_ok else "Pastikan setiap fakta terhubung ke bukti",
        ),
        _external(specs["POLICE_IDENTITY_EXTERNAL"]),
    ]


def assess(
    *,
    case_id: str,
    route: Route,
    facts: list[FactRecord],
    conflicts: list[ConflictRecord],
    evidence: list[EvidenceRecord],
    transactions: list[TransactionGroupRecord],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = profile or load_profile()
    usable = _usable_facts(facts)
    globals_checks = _global_checks(route=route, facts=usable, conflicts=conflicts)
    builders = {
        "BANK_PJP": lambda: _bank_checks(loaded, usable, evidence),
        "IASC": lambda: _iasc_checks(loaded, usable, evidence),
        "POLICE": lambda: _police_checks(loaded, usable, evidence, transactions),
    }
    channels: list[dict[str, Any]] = []
    for name in CHANNELS:
        checks = [*globals_checks, *builders[name]()]
        status = _channel_status(checks)
        met = sum(1 for item in checks if item.status == "MET")
        channels.append(
            {
                "channel": name,
                "label": str(loaded["channels"][name].get("label") or name),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "checks_met": met,
                "checks_total": len(checks),
                "checks": [item.as_public() for item in checks],
                "snapshot_checks": [item.as_snapshot() for item in checks],
            }
        )
    overall = "READY"
    if any(ch["status"] == "BLOCKED" for ch in channels):
        overall = "BLOCKED"
    elif any(ch["status"] == "NEEDS_ACTION" for ch in channels):
        overall = "NEEDS_ACTION"
    return {
        "case_id": case_id,
        "profile_version": loaded["profile_version"],
        "overall_status": overall,
        "overall_label": STATUS_LABELS[overall],
        "channels": channels,
        "official_status": "NOT_VERIFIED",
        "disclaimer": loaded["disclaimer"],
        "source_urls": loaded["source_urls"],
        "last_reviewed_at": loaded["last_reviewed_at"],
    }


def snapshot_readiness(report: dict[str, Any]) -> dict[str, Any]:
    channels = []
    for item in report["channels"]:
        channels.append(
            {
                "channel": item["channel"],
                "status": item["status"],
                "checks": sorted(item["snapshot_checks"], key=lambda row: str(row["check_id"])),
            }
        )
    payload = {
        "channels": sorted(channels, key=lambda row: str(row["channel"])),
        "overall_status": report["overall_status"],
        "profile_version": report["profile_version"],
    }
    canonical_json(payload)
    return payload


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": report["case_id"],
        "profile_version": report["profile_version"],
        "overall_status": report["overall_status"],
        "overall_label": report["overall_label"],
        "channels": [
            {
                "channel": ch["channel"],
                "label": ch["label"],
                "status": ch["status"],
                "status_label": ch["status_label"],
                "checks_met": ch["checks_met"],
                "checks_total": ch["checks_total"],
                "checks": ch["checks"],
            }
            for ch in report["channels"]
        ],
        "official_status": "NOT_VERIFIED",
        "disclaimer": report["disclaimer"],
    }


# --- Reporting Unit scoped readiness (V2) ---

def _unit_facts(unit, all_facts: list[FactRecord]) -> list[FactRecord]:
    fid_set = set(unit.fact_ids)
    return [f for f in all_facts if f.fact_id in fid_set]


def _unit_evidence(unit, all_evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
    eid_set = set(unit.evidence_ids)
    return [e for e in all_evidence if e.evidence_id in eid_set]


def _conflict_scope(conflict: ConflictRecord, units: list) -> str:
    # UNIT_SCOPED if all fact_ids belong to single unit
    from app.domain.models import ConflictScope

    fact_set = set(conflict.fact_ids)
    matching_units = [u for u in units if fact_set.issubset(set(u.fact_ids))]
    if len(matching_units) == 1:
        return ConflictScope.UNIT_SCOPED.value
    # if fact_ids span multiple units or none
    return ConflictScope.INCIDENT_GLOBAL.value


def assess_unit(
    unit,
    all_facts: list[FactRecord],
    all_evidence: list[EvidenceRecord],
    all_conflicts: list[ConflictRecord],
    all_units: list,
    route: Route,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-unit BANK_PJP + IASC assessment, with incident-global conflict handling."""
    from app.domain.models import MappingStatus

    loaded = profile or load_profile()
    unit_facts = _unit_facts(unit, all_facts)
    unit_evid = _unit_evidence(unit, all_evidence)
    # shared evidence: allow communication evidence from shared pool? For IASC need chat; if unit has no chat but incident has shared chat, should count?
    # For now, only unit evidence; shared chat not auto-bound. So missing.
    usable = _usable_facts(unit_facts) if unit_facts else []
    # Determine relevant conflicts: only those scoped to this unit or global
    # For unit isolation: BLOCKING conflict that is UNIT_SCOPED to other unit should NOT block this unit
    relevant_conflicts: list[ConflictRecord] = []
    for c in all_conflicts:
        if c.status.value != "OPEN" or c.severity.value != "BLOCKING":
            continue
        scope = _conflict_scope(c, all_units)
        if scope == "INCIDENT_GLOBAL":
            relevant_conflicts.append(c)
        elif set(c.fact_ids).issubset(set(unit.fact_ids)):
            relevant_conflicts.append(c)
        # else unit-scoped to other unit -> ignore

    # If mapping is AMBIGUOUS -> BLOCKED directly
    if getattr(unit, "mapping_status", None) == MappingStatus.AMBIGUOUS:
        # produce BLOCKED for financial channels
        channels = []
        for name in ("BANK_PJP", "IASC"):
            spec_block = {"check_id": f"{name}_MAPPING", "label": "Mapping transaksi jelas", "level": "REQUIRED"}
            blocking_check = _result(spec_block, "CONFLICT", f"AMBIGUOUS_MAPPING pada {unit.unit_id}", action="Pilih pasangan transaksi yang benar", blocking=True)
            status = "BLOCKED"
            channels.append(
                {
                    "channel": name,
                    "label": str(loaded["channels"][name].get("label") or name),
                    "status": status,
                    "status_label": STATUS_LABELS[status],
                    "checks_met": 0,
                    "checks_total": 1,
                    "checks": [blocking_check.as_public()],
                    "snapshot_checks": [blocking_check.as_snapshot()],
                }
            )
        overall = "BLOCKED"
        return {
            "unit_id": unit.unit_id,
            "overall_status": overall,
            "overall_label": STATUS_LABELS[overall],
            "channels": channels,
            "mapping_status": str(unit.mapping_status),
        }

    # Normal financial checks: BANK and IASC use unit facts/evidence plus global checks scoped to unit
    globals_for_unit = _global_checks(route=route, facts=usable, conflicts=relevant_conflicts)
    # For unit, we consider critical reviewed only for this unit's critical facts? Keep global but scoped.
    channels = []
    builders = {
        "BANK_PJP": lambda: _bank_checks(loaded, usable, unit_evid),
        "IASC": lambda: _iasc_checks(loaded, usable, unit_evid),
    }
    for name in ("BANK_PJP", "IASC"):
        raw_checks = builders[name]()
        # Shared incident communication: INCIDENT_SHARED vs UNIT_SCOPED
        # For IASC, communication and chronology may be satisfied by shared incident evidence (chat)
        # but we must not claim unit-specific verification without human confirmation
        if name == "IASC":
            global_chat = _named_evidence(all_evidence, ("chat", "pesan", "wa", "whatsapp"))
            global_unavailable_comm = _unavailable_of(_usable_facts(all_facts), {__import__("app.domain.models", fromlist=["FactType"]).FactType.CLAIM, __import__("app.domain.models", fromlist=["FactType"]).FactType.CHANNEL})
            patched = []
            for ck in raw_checks:
                if ck.check_id == "IASC_COMMUNICATION_EVIDENCE" and ck.status == "MISSING":
                    if global_chat or global_unavailable_comm:
                        # INCIDENT_SHARED: available but not unit-specific
                        patched.append(
                            _result(
                                {"check_id": ck.check_id, "label": ck.label, "level": ck.level},
                                "MET",
                                "Bukti komunikasi insiden tersedia (INCIDENT_SHARED) — belum terverifikasi khusus untuk unit ini",
                                evidence=global_chat or [],
                                blocking=False,
                            )
                        )
                        continue
                if ck.check_id == "IASC_CHRONOLOGY" and ck.status == "MISSING":
                    global_chrono = _reviewed_of(_usable_facts(all_facts), {__import__("app.domain.models", fromlist=["FactType"]).FactType.DATETIME, __import__("app.domain.models", fromlist=["FactType"]).FactType.EVENT, __import__("app.domain.models", fromlist=["FactType"]).FactType.CLAIM})
                    if global_chrono:
                        patched.append(_result({"check_id": ck.check_id, "label": ck.label, "level": ck.level}, "MET", "Kronologi tersusun dari fakta insiden (INCIDENT_SHARED)", facts=global_chrono))
                        continue
                patched.append(ck)
            raw_checks = patched
        checks = [*globals_for_unit, *raw_checks]
        if getattr(unit, "mapping_status", None) == MappingStatus.INCOMPLETE:
            pass
        status = _channel_status(checks)
        met = sum(1 for item in checks if item.status == "MET")
        channels.append(
            {
                "channel": name,
                "label": str(loaded["channels"][name].get("label") or name),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "checks_met": met,
                "checks_total": len(checks),
                "checks": [item.as_public() for item in checks],
                "snapshot_checks": [item.as_snapshot() for item in checks],
            }
        )
    overall = "READY"
    if any(ch["status"] == "BLOCKED" for ch in channels):
        overall = "BLOCKED"
    elif any(ch["status"] == "NEEDS_ACTION" for ch in channels):
        overall = "NEEDS_ACTION"
    return {
        "unit_id": unit.unit_id,
        "overall_status": overall,
        "overall_label": STATUS_LABELS[overall],
        "channels": channels,
        "mapping_status": str(getattr(unit, "mapping_status", "UNKNOWN")),
    }


def assess_units(
    *,
    case_id: str,
    units: list,
    facts: list[FactRecord],
    evidence: list[EvidenceRecord],
    conflicts: list[ConflictRecord],
    route: Route,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess all units for financial channels + incident POLICE."""
    loaded = profile or load_profile()
    # incident POLICE (uses all evidence)
    usable_all = _usable_facts(facts)
    transactions: list[Any] = []  # not used for police in v2, but keep
    police_checks = _police_checks(loaded, usable_all, evidence, transactions)  # type: ignore[arg-type]
    # Use global checks for police (incident-level)
    global_police = _global_checks(route=route, facts=usable_all, conflicts=conflicts)
    police_channel_checks = [*global_police, *police_checks]
    police_status = _channel_status(police_channel_checks)
    incident = {
        "channel": "POLICE",
        "label": str(loaded["channels"]["POLICE"].get("label") or "Kepolisian"),
        "status": police_status,
        "status_label": STATUS_LABELS[police_status],
        "checks_met": sum(1 for item in police_channel_checks if item.status == "MET"),
        "checks_total": len(police_channel_checks),
        "checks": [c.as_public() for c in police_channel_checks],
        "snapshot_checks": [c.as_snapshot() for c in police_channel_checks],
    }
    unit_reports = []
    readiness_by_unit: dict[str, dict[str, str]] = {}
    for unit in sorted(units, key=lambda u: u.unit_id):
        rep = assess_unit(unit, facts, evidence, conflicts, units, route, loaded)
        unit_reports.append(rep)
        readiness_by_unit[unit.unit_id] = {ch["channel"]: ch["status"] for ch in rep["channels"]}
    overall = "READY"
    if any(r["overall_status"] == "BLOCKED" for r in unit_reports) or police_status == "BLOCKED":
        overall = "BLOCKED"
    elif any(r["overall_status"] == "NEEDS_ACTION" for r in unit_reports) or police_status == "NEEDS_ACTION":
        overall = "NEEDS_ACTION"
    return {
        "case_id": case_id,
        "profile_version": loaded["profile_version"],
        "overall_status": overall,
        "overall_label": STATUS_LABELS[overall],
        "units": unit_reports,
        "incident_police": incident,
        "official_status": "NOT_VERIFIED",
        "disclaimer": loaded["disclaimer"],
        "source_urls": loaded["source_urls"],
        "last_reviewed_at": loaded["last_reviewed_at"],
        "readiness_by_unit": readiness_by_unit,
    }


def snapshot_units(report: dict[str, Any]) -> dict[str, Any]:
    units = []
    for u in report.get("units", []):
        channels = []
        for ch in u["channels"]:
            channels.append(
                {
                    "channel": ch["channel"],
                    "status": ch["status"],
                    "checks": sorted(ch["snapshot_checks"], key=lambda row: str(row["check_id"])),
                }
            )
        units.append(
            {
                "unit_id": u["unit_id"],
                "overall_status": u["overall_status"],
                "mapping_status": u.get("mapping_status"),
                "channels": sorted(channels, key=lambda row: str(row["channel"])),
            }
        )
    incident = None
    if "incident_police" in report:
        inc = report["incident_police"]
        incident = {
            "channel": inc["channel"],
            "status": inc["status"],
            "checks": sorted(inc["snapshot_checks"], key=lambda row: str(row["check_id"])),
        }
    payload = {
        "units": sorted(units, key=lambda row: str(row["unit_id"])),
        "incident_police": incident,
        "overall_status": report["overall_status"],
        "profile_version": report["profile_version"],
    }
    canonical_json(payload)
    return payload
