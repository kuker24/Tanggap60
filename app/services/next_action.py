from __future__ import annotations

from typing import Any

from app.domain.models import (
    ConflictRecord,
    ConflictScope,
    ConflictSeverity,
    ConflictStatus,
    MappingStatus,
    NextActionCode,
    NextBestAction,
    ReportingUnitRecord,
)


def _open_blocking(conflicts: list[ConflictRecord]) -> list[ConflictRecord]:
    return [c for c in conflicts if c.severity == ConflictSeverity.BLOCKING and c.status == ConflictStatus.OPEN]


def _unit_missing_fields(unit: ReportingUnitRecord) -> list[str]:
    missing: list[str] = []
    if unit.destination_account is None:
        missing.append("DESTINATION")
    if unit.amount is None:
        missing.append("AMOUNT")
    if unit.transferred_at is None:
        missing.append("DATETIME")
    return missing


def _conflict_scope(conflict: ConflictRecord, units: list[ReportingUnitRecord]) -> str:
    fact_set = set(conflict.fact_ids)
    matching = [u for u in units if fact_set.issubset(set(u.fact_ids))]
    if len(matching) == 1:
        return ConflictScope.UNIT_SCOPED.value
    return ConflictScope.INCIDENT_GLOBAL.value


def _ready_financial_units(
    units: list[ReportingUnitRecord],
    readiness_by_unit: dict[str, dict[str, Any]] | None,
) -> list[ReportingUnitRecord]:
    if not readiness_by_unit:
        return []
    ready: list[ReportingUnitRecord] = []
    for unit in units:
        if unit.mapping_status != MappingStatus.COMPLETE:
            continue
        r = readiness_by_unit.get(unit.unit_id, {})
        if r.get("BANK_PJP") == "READY" or r.get("IASC") == "READY":
            ready.append(unit)
    ready.sort(key=lambda u: u.unit_id)
    return ready


def recommend_next_action(
    *,
    case_id: str,
    units: list[ReportingUnitRecord],
    conflicts: list[ConflictRecord],
    readiness_by_unit: dict[str, dict[str, Any]] | None = None,
    incident_police_ready: bool = False,
) -> NextBestAction:
    # 1. Incident-global blocker has highest priority
    blocking = _open_blocking(conflicts)
    global_blocking: ConflictRecord | None = None
    scoped_blocking: list[tuple[ConflictRecord, str]] = []
    for c in blocking:
        scope = _conflict_scope(c, units)
        if scope == ConflictScope.INCIDENT_GLOBAL.value:
            global_blocking = c
            break
        else:
            # unit scoped
            # find which unit it belongs to
            for u in units:
                if set(c.fact_ids).issubset(set(u.fact_ids)):
                    scoped_blocking.append((c, u.unit_id))
                    break
            else:
                # no unit match but not global, treat as global
                global_blocking = c
                break
    if global_blocking:
        return NextBestAction(
            code=NextActionCode.RESOLVE_CONFLICT,
            label="Selesaikan konflik bukti",
            reason="Ada konflik wajib yang memblokir seluruh insiden — selesaikan di tinjauan fakta.",
            priority=1,
            related_fact_ids=global_blocking.fact_ids,
        )

    # 2. Any unaffected READY financial unit should be acted on immediately
    ready_units = _ready_financial_units(units, readiness_by_unit)
    if ready_units:
        # Ensure ready unit is not affected by scoped conflict
        # If ready unit has no scoped conflict, it is unaffected
        unaffected_ready: list[ReportingUnitRecord] = []
        for u in ready_units:
            # check if this unit has a scoped blocking conflict
            has_scoped = any(unit_id == u.unit_id for _, unit_id in scoped_blocking)
            if not has_scoped:
                unaffected_ready.append(u)
        # If at least one unaffected ready exists, act on it
        acting_pool = unaffected_ready if unaffected_ready else []
        if acting_pool:
            target = acting_pool[0]
            r = (readiness_by_unit or {}).get(target.unit_id, {})
            if r.get("BANK_PJP") == "READY":
                return NextBestAction(
                    code=NextActionCode.CONTACT_BANK_PJP,
                    label="Hubungi bank untuk transaksi yang sudah siap",
                    reason="Ada transaksi yang banknya sudah siap — hubungi bank lewat situs resmi sekarang, jangan menunggu transaksi lain.",
                    target_unit_id=target.unit_id,
                    priority=2,
                )
            if r.get("IASC") == "READY":
                return NextBestAction(
                    code=NextActionCode.PREPARE_IASC_UNIT,
                    label="Siapkan laporan IASC yang sudah siap",
                    reason="Ada transaksi yang siap dilaporkan ke IASC — buka portal resmi IASC dan isi datanya, jangan menunggu transaksi lain.",
                    target_unit_id=target.unit_id,
                    priority=2,
                )
            return NextBestAction(
                code=NextActionCode.PREPARE_IASC_UNIT,
                label="Tindak lanjuti transaksi yang siap",
                reason="Ada transaksi yang sudah siap — jangan menunggu transaksi lain.",
                target_unit_id=target.unit_id,
                priority=2,
            )
        # if all ready are affected by scoped conflict, fall through to scoped handling next

    # 3. Unit-scoped blocker where no other ready unaffected exists
    if scoped_blocking:
        # no unaffected ready, so need to resolve scoped conflict for its unit
        c, unit_id = sorted(scoped_blocking, key=lambda x: x[1])[0]
        return NextBestAction(
            code=NextActionCode.RESOLVE_CONFLICT,
            label="Selesaikan data yang bentrok",
            reason="Ada data yang saling bertentangan — pilih yang benar supaya paketnya akurat.",
            priority=3,
            target_unit_id=unit_id,
            related_fact_ids=c.fact_ids,
        )

    # 4. Ambiguous unit where no other ready unaffected exists
    ambiguous = [u for u in units if u.mapping_status == MappingStatus.AMBIGUOUS]
    if ambiguous:
        # if we have no unaffected ready above, then need to resolve ambiguous
        target = sorted(ambiguous, key=lambda x: x.unit_id)[0]
        return NextBestAction(
            code=NextActionCode.RESOLVE_UNIT_MAPPING,
            label="Tentukan pasangan transaksi",
            reason="Ada transaksi yang belum terpasang — pilih pasangan jumlah uang, rekening, dan waktu yang benar. Kami tidak akan menebak.",
            target_unit_id=target.unit_id,
            priority=4,
            related_fact_ids=target.fact_ids,
            related_evidence_ids=target.evidence_ids,
        )

    # 5. Incomplete critical field
    incomplete_units = [u for u in units if u.mapping_status == MappingStatus.INCOMPLETE]
    incomplete_units.sort(key=lambda u: u.unit_id)
    for unit in incomplete_units:
        missing = _unit_missing_fields(unit)
        if "AMOUNT" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_TRANSACTION_AMOUNT,
                label="Konfirmasi nominal transfer",
                reason="Ada transaksi yang jumlah uangnya belum jelas.",
                target_unit_id=unit.unit_id,
                priority=5,
                related_fact_ids=unit.fact_ids,
            )
        if "DATETIME" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_TRANSACTION_TIME,
                label="Konfirmasi waktu transaksi",
                reason="Ada transaksi yang waktunya belum jelas.",
                target_unit_id=unit.unit_id,
                priority=5,
                related_fact_ids=unit.fact_ids,
            )
        if "DESTINATION" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_DESTINATION,
                label="Konfirmasi rekening tujuan",
                reason="Ada transaksi yang rekening tujuannya belum jelas.",
                target_unit_id=unit.unit_id,
                priority=5,
                related_fact_ids=unit.fact_ids,
            )
        return NextBestAction(
            code=NextActionCode.ADD_TRANSFER_EVIDENCE,
            label="Tambah bukti transfer",
            reason="Ada transaksi yang belum lengkap — tambah bukti transfer yang jelas.",
            target_unit_id=unit.unit_id,
            priority=5,
        )

    # 6. Police incident preparation
    if units and not incident_police_ready:
        return NextBestAction(
            code=NextActionCode.PREPARE_POLICE_INCIDENT,
            label="Siapkan ringkasan untuk polisi",
            reason="Setelah urusan bank, siapkan kronologi lengkap untuk situs resmi kepolisian.",
            priority=6,
        )

    # If no units at all, suggest adding evidence
    if not units:
        return NextBestAction(
            code=NextActionCode.ADD_TRANSFER_EVIDENCE,
            label="Tambah bukti transfer",
            reason="Belum ada transaksi — kirim bukti transfer yang memuat jumlah uang, rekening, dan waktu.",
            priority=7,
        )

    # Fallback: approve ready unit or download pack
    complete_units = [u for u in units if u.mapping_status == MappingStatus.COMPLETE]
    if complete_units:
        target = sorted(complete_units, key=lambda u: u.unit_id)[0]
        return NextBestAction(
            code=NextActionCode.APPROVE_READY_UNIT,
            label="Setujui transaksi yang sudah siap",
            reason="Ada transaksi menunggu persetujuan untuk dibuatkan paket terverifikasi.",
            target_unit_id=target.unit_id,
            priority=8,
        )

    return NextBestAction(
        code=NextActionCode.DOWNLOAD_VERIFIED_PACK,
        label="Unduh paket terverifikasi",
        reason="Semua unit sudah diproses — unduh ZIP dan lakukan handoff manual.",
        priority=9,
    )


def next_action_to_dict(action: NextBestAction) -> dict[str, Any]:
    return {
        "code": action.code.value if hasattr(action.code, "value") else str(action.code),
        "label": action.label,
        "reason": action.reason,
        "target_unit_id": action.target_unit_id,
        "priority": action.priority,
        "related_fact_ids": action.related_fact_ids or [],
        "related_evidence_ids": action.related_evidence_ids or [],
    }
