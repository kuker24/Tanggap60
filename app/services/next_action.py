from __future__ import annotations

from typing import Any

from app.domain.models import (
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    MappingStatus,
    NextActionCode,
    NextBestAction,
    ReportingUnitRecord,
)

# Priority policy
# 1 blocking conflict -> RESOLVE_CONFLICT
# 2 ambiguous mapping -> RESOLVE_UNIT_MAPPING
# 3 missing critical transaction fact -> CONFIRM_*
# 4 one ready financial unit -> CONTACT_BANK_PJP / PREPARE_IASC_UNIT
# 5 after urgent financial -> PREPARE_POLICE_INCIDENT


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


def recommend_next_action(
    *,
    case_id: str,
    units: list[ReportingUnitRecord],
    conflicts: list[ConflictRecord],
    readiness_by_unit: dict[str, dict[str, Any]] | None = None,
    incident_police_ready: bool = False,
) -> NextBestAction:
    # Priority 1: blocking conflict
    blocking = _open_blocking(conflicts)
    if blocking:
        # If conflict is unit-scoped vs incident? we treat any blocking as global priority
        # But per prompt, unit isolation: if conflict only affects unit B, unit A ready should still be actionable
        # So we need to distinguish. However for now, blocking conflict always top priority
        # To allow unit isolation, we will check if blocking conflict scopes only to incomplete units?
        # Simple: return RESOLVE_CONFLICT with first blocking
        return NextBestAction(
            code=NextActionCode.RESOLVE_CONFLICT,
            label="Selesaikan konflik bukti",
            reason=f"Ada {len(blocking)} konflik wajib yang masih terbuka — selesaikan di tinjauan fakta.",
            priority=1,
            related_fact_ids=blocking[0].fact_ids,
        )

    # Priority 2: ambiguous mapping
    ambiguous = [u for u in units if u.mapping_status == MappingStatus.AMBIGUOUS]
    if ambiguous:
        # pick first ambiguous unit deterministically (sorted)
        target = sorted(ambiguous, key=lambda x: x.unit_id)[0]
        return NextBestAction(
            code=NextActionCode.RESOLVE_UNIT_MAPPING,
            label="Tentukan pasangan transaksi",
            reason=f"Unit {target.unit_id} memiliki AMBIGUOUS_MAPPING — pilih pasangan nominal, rekening, dan waktu yang benar.",
            target_unit_id=target.unit_id,
            priority=2,
            related_fact_ids=target.fact_ids,
            related_evidence_ids=target.evidence_ids,
        )

    # Priority 3: ready financial unit (so ready can be acted without waiting for other incomplete)
    if readiness_by_unit:
        ready_units = []
        for unit in units:
            if unit.mapping_status != MappingStatus.COMPLETE:
                continue
            r = readiness_by_unit.get(unit.unit_id, {})
            bank = r.get("BANK_PJP")
            iasc = r.get("IASC")
            if bank == "READY" or iasc == "READY":
                ready_units.append(unit)
        ready_units.sort(key=lambda u: u.unit_id)
        if ready_units:
            target = ready_units[0]
            r = readiness_by_unit.get(target.unit_id, {})
            if r.get("BANK_PJP") == "READY":
                return NextBestAction(
                    code=NextActionCode.CONTACT_BANK_PJP,
                    label="Hubungi bank/PJP untuk unit siap",
                    reason=f"Unit {target.unit_id} sudah READY untuk jalur finansial — segera hubungi bank/PJP via kanal resmi.",
                    target_unit_id=target.unit_id,
                    priority=3,
                )
            if r.get("IASC") == "READY":
                return NextBestAction(
                    code=NextActionCode.PREPARE_IASC_UNIT,
                    label="Siapkan laporan IASC untuk unit siap",
                    reason=f"Unit {target.unit_id} READY untuk IASC — buka portal resmi IASC dan isi data.",
                    target_unit_id=target.unit_id,
                    priority=3,
                )
            return NextBestAction(
                code=NextActionCode.PREPARE_IASC_UNIT,
                label="Tindak lanjuti unit siap",
                reason=f"Unit {target.unit_id} sudah siap — jangan menunggu unit lain.",
                target_unit_id=target.unit_id,
                priority=3,
            )

    # Priority 4: missing critical transaction fact per unit
    incomplete_units = [u for u in units if u.mapping_status == MappingStatus.INCOMPLETE]
    incomplete_units.sort(key=lambda u: u.unit_id)
    for unit in incomplete_units:
        missing = _unit_missing_fields(unit)
        if "AMOUNT" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_TRANSACTION_AMOUNT,
                label="Konfirmasi nominal transfer",
                reason=f"Unit {unit.unit_id} belum memiliki nominal yang ditinjau.",
                target_unit_id=unit.unit_id,
                priority=4,
                related_fact_ids=unit.fact_ids,
            )
        if "DATETIME" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_TRANSACTION_TIME,
                label="Konfirmasi waktu transaksi",
                reason=f"Unit {unit.unit_id} belum memiliki waktu transaksi yang ditinjau.",
                target_unit_id=unit.unit_id,
                priority=4,
                related_fact_ids=unit.fact_ids,
            )
        if "DESTINATION" in missing:
            return NextBestAction(
                code=NextActionCode.CONFIRM_DESTINATION,
                label="Konfirmasi rekening tujuan",
                reason=f"Unit {unit.unit_id} belum memiliki rekening tujuan yang ditinjau.",
                target_unit_id=unit.unit_id,
                priority=4,
                related_fact_ids=unit.fact_ids,
            )
        return NextBestAction(
            code=NextActionCode.ADD_TRANSFER_EVIDENCE,
            label="Tambahkan bukti transfer",
            reason=f"Unit {unit.unit_id} belum lengkap — unggah bukti transaksi yang jelas.",
            target_unit_id=unit.unit_id,
            priority=4,
        )

    # Priority 4 fallback already handled; next is police
    # Determine which units are READY for BANK_PJP or IASC
    # readiness_by_unit maps unit_id -> {BANK_PJP: status, IASC: status}
    if readiness_by_unit:
        ready_units = []
        for unit in units:
            if unit.mapping_status != MappingStatus.COMPLETE:
                continue
            r = readiness_by_unit.get(unit.unit_id, {})
            bank = r.get("BANK_PJP")
            iasc = r.get("IASC")
            if bank == "READY" or iasc == "READY":
                ready_units.append(unit)
        ready_units.sort(key=lambda u: u.unit_id)
        if ready_units:
            target = ready_units[0]
            r = readiness_by_unit.get(target.unit_id, {})
            # Prefer BANK first, then IASC
            if r.get("BANK_PJP") == "READY":
                return NextBestAction(
                    code=NextActionCode.CONTACT_BANK_PJP,
                    label="Hubungi bank/PJP untuk unit siap",
                    reason=f"Unit {target.unit_id} sudah READY untuk jalur finansial — segera hubungi bank/PJP via kanal resmi.",
                    target_unit_id=target.unit_id,
                    priority=4,
                )
            if r.get("IASC") == "READY":
                return NextBestAction(
                    code=NextActionCode.PREPARE_IASC_UNIT,
                    label="Siapkan laporan IASC untuk unit siap",
                    reason=f"Unit {target.unit_id} READY untuk IASC — buka portal resmi IASC dan isi data.",
                    target_unit_id=target.unit_id,
                    priority=4,
                )
            # generic
            return NextBestAction(
                code=NextActionCode.PREPARE_IASC_UNIT,
                label="Tindak lanjuti unit siap",
                reason=f"Unit {target.unit_id} sudah siap — jangan menunggu unit lain.",
                target_unit_id=target.unit_id,
                priority=4,
            )

    # Priority 5: police incident
    # If no ready financial but we have units, or after financial handled
    if units and not incident_police_ready:
        # Check if financial units are done? For now suggest police
        return NextBestAction(
            code=NextActionCode.PREPARE_POLICE_INCIDENT,
            label="Siapkan paket incident untuk kepolisian",
            reason="Setelah jalur finansial, siapkan kronologi lengkap untuk kanal kepolisian — pilih kanal resmi yang tersedia.",
            priority=5,
        )

    # If no units at all, suggest adding evidence
    if not units:
        return NextBestAction(
            code=NextActionCode.ADD_TRANSFER_EVIDENCE,
            label="Unggah bukti transfer",
            reason="Belum ada unit transaksi — unggah bukti transfer yang memuat nominal, rekening, dan waktu.",
            priority=6,
        )

    # Fallback: approve ready unit
    # Find any complete unit
    complete_units = [u for u in units if u.mapping_status == MappingStatus.COMPLETE]
    if complete_units:
        target = sorted(complete_units, key=lambda u: u.unit_id)[0]
        return NextBestAction(
            code=NextActionCode.APPROVE_READY_UNIT,
            label="Setujui unit yang sudah siap",
            reason=f"Unit {target.unit_id} menunggu persetujuan untuk menghasilkan paket terverifikasi.",
            target_unit_id=target.unit_id,
            priority=7,
        )

    # Final fallback
    return NextBestAction(
        code=NextActionCode.DOWNLOAD_VERIFIED_PACK,
        label="Unduh paket terverifikasi",
        reason="Semua unit sudah diproses — unduh ZIP dan lakukan handoff manual.",
        priority=8,
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
