from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.errors import ApprovalRequired, ArtifactVerifyFailed
from app.domain.models import ArtifactRecord, ArtifactType, ReviewStatus, VerifyStatus
from app.domain.policies import contains_absolute_copy, sha256_bytes
from app.domain.states import Route, State
from app.infrastructure.repositories import (
    ActionRepository,
    ArtifactRepository,
    CaseRepository,
    ConflictRepository,
    EvidenceRepository,
    FactRepository,
    TransactionRepository,
)
from app.infrastructure.storage import CaseStorage
from app.services.approval import ApprovalService
from app.services.cases import now_utc
from app.services.ids import new_id
from app.services.readiness import assess, public_report
from app.templates.pdf import render_lines
from app.web.labels import human, soften

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json"
ZIP_DATE = (2026, 9, 2, 0, 0, 0)


class ArtifactService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: CaseStorage,
        approval: ApprovalService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.approval = approval
        self.cases = CaseRepository(session)
        self.facts = FactRepository(session)
        self.conflicts = ConflictRepository(session)
        self.actions = ActionRepository(session)
        self.evidence = EvidenceRepository(session)
        self.transactions = TransactionRepository(session)
        self.artifacts = ArtifactRepository(session)

    def compile(self, case_id: str, snapshot_hash: str) -> list[ArtifactRecord]:
        case = self.cases.get(case_id)
        if not case.approved_snapshot_hash:
            raise ApprovalRequired("butuh persetujuan")
        if case.approved_snapshot_hash != snapshot_hash:
            raise ArtifactVerifyFailed("hash persetujuan berbeda")
        # try case-level snapshot first, then unit-level if target approval is unit-scoped
        try:
            payload, digest = self.approval.current_snapshot(case_id)
            if digest != snapshot_hash:
                # try unit snapshots
                from app.infrastructure.repositories import ApprovalRepository

                all_ap = ApprovalRepository(self.session).list_for_case(case_id)
                matched = next((a for a in all_ap if a.snapshot_hash == snapshot_hash and a.revoked_at is None), None)
                if matched and matched.target_id:
                    payload, digest = self.approval.unit_snapshot(case_id, matched.target_id)
                if digest != snapshot_hash:
                    raise ArtifactVerifyFailed("snapshot berubah")
            else:
                payload = payload  # case-level ok
        except ArtifactVerifyFailed:
            raise
        except Exception as exc:
            raise ArtifactVerifyFailed("snapshot berubah") from exc
        generated_at = now_utc().isoformat()
        existing = self.artifacts.list_for_case(case_id)
        if existing and all(a.source_snapshot_hash == snapshot_hash for a in existing):
            return existing
        built: list[ArtifactRecord] = []
        # Determine if we should use 2.2 reporting-units path
        use_units = False
        units = []
        units_report = None
        next_action_payload = None
        if case.route == Route.POST_INCIDENT_RESPONSE:
            try:
                from app.infrastructure.repositories import UnitMappingRepository
                from app.services.next_action import next_action_to_dict, recommend_next_action
                from app.services.readiness import assess_units
                from app.services.reporting_units import compile_reporting_units

                raw_facts = self.facts.list_for_case(case_id)
                raw_evidence = self.evidence.list_for_case(case_id)
                raw_conflicts = self.conflicts.list_for_case(case_id)
                mappings = UnitMappingRepository(self.session).list_for_case(case_id)
                decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
                units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
                # Use 2.2 only for multi-unit or ambiguous cases; keep single complete unit as 2.1 for backward compat
                if len(units) > 1 or any(getattr(u, "mapping_status", None) != "COMPLETE" for u in units):
                    use_units = bool(units)
                    if use_units:
                        units_report = assess_units(case_id=case_id, units=units, facts=raw_facts, evidence=raw_evidence, conflicts=raw_conflicts, route=case.route)
                        next_act = recommend_next_action(
                            case_id=case_id,
                            units=units,
                            conflicts=raw_conflicts,
                            readiness_by_unit=units_report.get("readiness_by_unit"),
                            incident_police_ready=(units_report.get("incident_police", {}).get("status") == "READY"),
                        )
                        next_action_payload = next_action_to_dict(next_act)
                elif len(units) == 1 and units[0].mapping_status == "COMPLETE":
                    # single complete unit stays 2.1
                    use_units = False
                    units = []
                else:
                    use_units = bool(units)
                    if use_units:
                        units_report = assess_units(case_id=case_id, units=units, facts=raw_facts, evidence=raw_evidence, conflicts=raw_conflicts, route=case.route)
                        next_act = recommend_next_action(
                            case_id=case_id,
                            units=units,
                            conflicts=raw_conflicts,
                            readiness_by_unit=units_report.get("readiness_by_unit"),
                            incident_police_ready=(units_report.get("incident_police", {}).get("status") == "READY"),
                        )
                        next_action_payload = next_action_to_dict(next_act)
            except Exception:
                use_units = False
        if case.route == Route.PRE_INCIDENT_CHECK:
            built.append(self._store_pdf(case_id, ArtifactType.VERIFICATION_BRIEF, self._brief_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
        elif use_units:
            # 2.2 path: per-unit packs
            report = self._readiness(case_id)
            # action plan reflects next best action
            built.append(self._store_pdf(case_id, ArtifactType.ACTION_PLAN, self._plan_lines_v2(case_id, snapshot_hash, next_action_payload, units), generated_at, snapshot_hash))
            built.append(self._store_pdf(case_id, ArtifactType.EVIDENCE_PACK, self._pack_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.READINESS_REPORT,
                    self._readiness_lines_v2(case_id, snapshot_hash, units_report),
                    generated_at,
                    snapshot_hash,
                )
            )
            # incident police pack
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.POLICE_HANDOFF_PACK,
                    self._channel_pack_lines(case_id, snapshot_hash, report, "POLICE"),
                    generated_at,
                    snapshot_hash,
                )
            )
            # per-unit packs
            for unit in units:
                # unit.json
                unit_data = self._unit_json_data(unit, units_report)
                built.append(
                    self._store_bytes(
                        case_id,
                        ArtifactType.REPORTING_UNIT_JSON,
                        json.dumps(unit_data, ensure_ascii=False, indent=2).encode(),
                        "application/json",
                        snapshot_hash,
                        f"units/{unit.unit_id}/unit.json",
                    )
                )
                # bank pack per unit
                urep = None
                if units_report:
                    urep = next((r for r in units_report["units"] if r["unit_id"] == unit.unit_id), None)  # type: ignore[index]
                bank_ready = urep and any(ch["channel"] == "BANK_PJP" and ch["status"] == "READY" for ch in urep["channels"])  # type: ignore[union-attr]
                iasc_ready = urep and any(ch["channel"] == "IASC" and ch["status"] == "READY" for ch in urep["channels"])  # type: ignore[union-attr]
                built.append(
                    self._store_pdf(
                        case_id,
                        ArtifactType.UNIT_BANK_PACK,
                        self._unit_pack_lines(case_id, snapshot_hash, unit, urep, "BANK", bank_ready),
                        generated_at,
                        snapshot_hash,
                        filename=f"units/{unit.unit_id}/bank_handoff_pack.pdf",
                    )
                )
                built.append(
                    self._store_pdf(
                        case_id,
                        ArtifactType.UNIT_IASC_PACK,
                        self._unit_pack_lines(case_id, snapshot_hash, unit, urep, "IASC", iasc_ready),
                        generated_at,
                        snapshot_hash,
                        filename=f"units/{unit.unit_id}/iasc_handoff_pack.pdf",
                    )
                )
        else:
            report = self._readiness(case_id)
            built.append(self._store_pdf(case_id, ArtifactType.ACTION_PLAN, self._plan_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
            built.append(self._store_pdf(case_id, ArtifactType.EVIDENCE_PACK, self._pack_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.READINESS_REPORT,
                    self._readiness_lines(case_id, snapshot_hash, report),
                    generated_at,
                    snapshot_hash,
                )
            )
            for artifact_type, channel in (
                (ArtifactType.BANK_HANDOFF_PACK, "BANK_PJP"),
                (ArtifactType.IASC_HANDOFF_PACK, "IASC"),
                (ArtifactType.POLICE_HANDOFF_PACK, "POLICE"),
            ):
                built.append(
                    self._store_pdf(
                        case_id,
                        artifact_type,
                        self._channel_pack_lines(case_id, snapshot_hash, report, channel),
                        generated_at,
                        snapshot_hash,
                    )
                )
        case_json = self._case_json(case_id, snapshot_hash, generated_at)
        built.append(self._store_bytes(case_id, ArtifactType.CASE_JSON, json.dumps(case_json, ensure_ascii=False, indent=2).encode(), "application/json", snapshot_hash, "case.json"))
        checklist = self._checklist_text(case_id)
        built.append(self._store_bytes(case_id, ArtifactType.CHECKLIST, checklist.encode(), "text/markdown", snapshot_hash, "handoff.md"))
        file_map = {str(a.verify_details.get("filename", a.type.value.lower())): (a.storage_key, a.sha256) for a in built}
        manifest_lines = [f"{sha}  {name}" for name, (_key, sha) in sorted(file_map.items())]
        manifest_body = "\n".join(manifest_lines) + "\n"
        built.append(self._store_bytes(case_id, ArtifactType.MANIFEST, manifest_body.encode(), "text/plain", snapshot_hash, "manifest.sha256"))
        zip_bytes = self._zip_bytes(case_id, built)
        built.append(self._store_bytes(case_id, ArtifactType.CASE_ZIP, zip_bytes, "application/zip", snapshot_hash, "case-pack.zip"))
        return built

    def _store_pdf(
        self,
        case_id: str,
        artifact_type: ArtifactType,
        lines: list[str],
        generated_at: str,
        snapshot_hash: str,
        filename: str | None = None,
    ) -> ArtifactRecord:
        self._assert_safe_copy("\n".join(lines))
        data = render_lines(human(artifact_type.value, "artifact"), lines, generated_at, snapshot_hash)
        name = filename or f"{artifact_type.value.lower()}.pdf"
        return self._store_bytes(case_id, artifact_type, data, "application/pdf", snapshot_hash, name)

    def _store_bytes(
        self,
        case_id: str,
        artifact_type: ArtifactType,
        data: bytes,
        mime: str,
        snapshot_hash: str,
        filename: str,
    ) -> ArtifactRecord:
        if mime.startswith("text/") or mime == "application/json":
            self._assert_safe_copy(data.decode("utf-8", errors="replace"))
        key = self.storage.new_key()
        self.storage.write_atomic(case_id, key, data)
        record = ArtifactRecord(
            artifact_id=new_id("art"),
            case_id=case_id,
            type=artifact_type,
            storage_key=key,
            mime=mime,
            size_bytes=len(data),
            sha256=sha256_bytes(data),
            source_snapshot_hash=snapshot_hash,
            verify_status=VerifyStatus.PENDING,
            verify_details={"filename": filename},
        )
        self.artifacts.add(record)
        return record

    def _plan_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        actions = self.actions.list_for_case(case_id)
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED",
            "SatuAman membantu menyusun langkah. Tidak ada jaminan dana kembali. Tidak mengirim laporan.",
            "## Lakukan sekarang",
        ]
        now = [a for a in actions if a.priority.value == "NOW"]
        nxt = [a for a in actions if a.priority.value == "NEXT"]
        later = [a for a in actions if a.priority.value == "LATER"]
        if now:
            lines.extend(f"- {soften(a.instruction)}" for a in now)
        else:
            lines.append("- Belum ada langkah segera.")
        lines.append("## Berikutnya")
        if nxt:
            lines.extend(f"- {soften(a.instruction)}" for a in nxt)
        else:
            lines.append("- Belum ada.")
        lines.append("## Setelah itu")
        if later:
            lines.extend(f"- {soften(a.instruction)}" for a in later)
        else:
            lines.append("- Belum ada.")
        return lines

    def _pack_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        facts = [f for f in self.facts.list_for_case(case_id) if f.review_status != ReviewStatus.CANDIDATE]
        conflicts = self.conflicts.list_for_case(case_id)
        txs = self.transactions.list_for_case(case_id)
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED",
            "Draf pengguna. Bukan laporan polisi dan bukan keputusan hukum.",
            "## Fakta yang ditinjau",
        ]
        if facts:
            lines.extend(f"- {_fact_line(f)}" for f in facts)
        else:
            lines.append("- Belum ada fakta yang dikunci.")
        lines.append("## Konflik")
        if conflicts:
            lines.extend(
                f"- {human(c.type.value, 'conflict')} ({'masih terbuka' if c.status.value == 'OPEN' else 'selesai'})"
                for c in conflicts
            )
        else:
            lines.append("- Tidak ada konflik tercatat.")
        lines.append("## Transaksi")
        if txs:
            lines.extend(
                f"- Tujuan {tx.destination_account or 'belum ada'} · {_money(tx.amount)}"
                for tx in txs
            )
        else:
            lines.append("- Belum ada transaksi terpasang.")
        return lines

    def _brief_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        facts = self.facts.list_for_case(case_id)
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED",
            "Hasil ini menunjukkan indikator dari pemeriksaan terbatas. Tidak menjamin aman dan tidak menetapkan penipuan.",
            "## Klaim dan entitas",
        ]
        if facts:
            lines.extend(f"- {_fact_line(f)}" for f in facts)
        else:
            lines.append("- Belum ada data.")
        lines.append("Pemeriksaan dapat keliru. Cek ulang di kanal resmi.")
        return lines

    def _checklist_text(self, case_id: str) -> str:
        return (
            "# Daftar periksa sebelum lapor\n\n"
            "Draf pengguna. Bukan dokumen resmi. Status resmi: NOT_VERIFIED.\n\n"
            "- [ ] Kronologi dan waktu kejadian sudah ditinjau.\n"
            "- [ ] Data rekening korban siap diisi di portal resmi.\n"
            "- [ ] Data rekening tujuan tersedia.\n"
            "- [ ] Nominal dan waktu transaksi terkonfirmasi.\n"
            "- [ ] Bukti transaksi tersedia.\n"
            "- [ ] Bukti komunikasi tersedia.\n"
            "- [ ] Identitas/KTP disiapkan untuk portal resmi, bukan diunggah ke SatuAman.\n\n"
            "## Kanal resmi\n"
            f"- IASC: {self.settings.official_iasc_url}\n"
            "- Laporan polisi: Anda yang mengirim.\n"
            "- Bank: nomor resmi dari aplikasi, kartu, atau situs resmi.\n"
        )

    def _case_json(self, case_id: str, snapshot_hash: str, generated_at: str) -> dict[str, Any]:
        case = self.cases.get(case_id)
        facts = self.facts.list_for_case(case_id)
        conflicts = self.conflicts.list_for_case(case_id)
        actions = self.actions.list_for_case(case_id)
        txs = self.transactions.list_for_case(case_id)
        artifacts = self.artifacts.list_for_case(case_id)
        active = self.approval.approvals.active_for_case(case_id)
        if active is None:
            # try any active unit approval as fallback
            from app.infrastructure.repositories import ApprovalRepository

            all_approvals = ApprovalRepository(self.session).list_for_case(case_id)
            actives = [a for a in all_approvals if a.revoked_at is None]
            active = actives[-1] if actives else None
        if active is None:
            raise ArtifactVerifyFailed("persetujuan tidak ditemukan")
        # try to build reporting units for 2.2
        reporting_units_payload = None
        next_best_action_payload = None
        schema_version = "2.1" if case.route == Route.POST_INCIDENT_RESPONSE else "2.0"
        try:
            from app.infrastructure.repositories import UnitMappingRepository
            from app.services.readiness import assess_units
            from app.services.reporting_units import compile_reporting_units, unit_to_dict

            raw_facts = self.facts.list_for_case(case_id)
            raw_evidence = self.evidence.list_for_case(case_id)
            raw_conflicts = self.conflicts.list_for_case(case_id)
            mappings = UnitMappingRepository(self.session).list_for_case(case_id)
            decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
            units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
            should_use_22 = bool(units) and (len(units) > 1 or any(getattr(u, "mapping_status", None) != "COMPLETE" for u in units))
            if should_use_22 and case.route == Route.POST_INCIDENT_RESPONSE:
                schema_version = "2.2"
                units_report = assess_units(case_id=case_id, units=units, facts=raw_facts, evidence=raw_evidence, conflicts=raw_conflicts, route=case.route)
                reporting_units_payload = [unit_to_dict(u) for u in units]
                # attach readiness per unit
                for ru in reporting_units_payload:
                    urep = next((r for r in units_report["units"] if r["unit_id"] == ru["unit_id"]), None)
                    ru["readiness"] = urep
                from app.services.next_action import next_action_to_dict, recommend_next_action

                nxt = recommend_next_action(
                    case_id=case_id,
                    units=units,
                    conflicts=raw_conflicts,
                    readiness_by_unit=units_report.get("readiness_by_unit"),
                    incident_police_ready=(units_report.get("incident_police", {}).get("status") == "READY"),
                )
                next_best_action_payload = next_action_to_dict(nxt)
        except Exception:
            pass
        base = {
            "schema_version": schema_version,
            "case_id": case.case_id,
            "mode": case.mode.value,
            "route": case.route.value,
            "state": State.HANDOFF_READY.value,
            "created_at": case.created_at.isoformat(),
            "updated_at": generated_at,
            "facts": [
                {
                    "fact_id": f.fact_id,
                    "type": f.type.value,
                    "raw_value": f.raw_value,
                    "normalized_value": _json_num(f.normalized_value) if f.type.value == "AMOUNT" else f.normalized_value,
                    "confidence": f.confidence,
                    "criticality": f.criticality.value,
                    "review_status": f.review_status.value,
                    "source": {
                        "evidence_id": f.source_evidence_id,
                        "locator": f.source_bbox or f"p{f.source_page or 1}",
                        "excerpt_hash": f.source_excerpt_hash,
                    },
                }
                for f in facts
                if f.review_status != ReviewStatus.CANDIDATE
            ],
            "conflicts": [
                {
                    "conflict_id": c.conflict_id,
                    "type": c.type.value,
                    "fact_ids": c.fact_ids,
                    "severity": c.severity.value,
                    "status": c.status.value,
                    "resolution_fact_id": c.resolution_fact_id,
                }
                for c in conflicts
            ],
            "transactions": [
                {
                    "transaction_id": t.transaction_group_id,
                    "victim_account": t.victim_account,
                    "destination_account": t.destination_account,
                    "amount": t.amount,
                    "currency": "IDR",
                    "transferred_at": transaction_time_or_none(t.transferred_at),
                }
                for t in txs
            ],
            "actions": [
                {
                    "action_id": a.action_id,
                    "priority": a.priority.value,
                    "channel": a.channel.value,
                    "instruction": a.instruction,
                    "status": a.status.value,
                }
                for a in actions
            ],
            "approval": {
                "actor": "USER",
                "scope": active.scope.value,
                "snapshot_hash": snapshot_hash,
                "approved_at": active.approved_at.isoformat(),
                "notice_version": active.notice_version,
            },
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "type": a.type.value,
                    "sha256": a.sha256,
                    "verify_status": a.verify_status.value,
                    "source_snapshot_hash": a.source_snapshot_hash,
                }
                for a in artifacts
            ],
            "receipt": None,
            "official_status": "NOT_VERIFIED",
            "disclaimer": "Tanggap60 tidak mengirim laporan dan tidak memverifikasi status resmi tiket.",
            "readiness": public_report(self._readiness(case_id)) if case.route == Route.POST_INCIDENT_RESPONSE else None,
        }
        if schema_version == "2.2":
            base["reporting_units"] = reporting_units_payload or []
            base["next_best_action"] = next_best_action_payload
            # incident readiness for v2
            try:
                from app.infrastructure.repositories import UnitMappingRepository
                from app.services.readiness import assess_units
                from app.services.reporting_units import compile_reporting_units

                raw_facts = self.facts.list_for_case(case_id)
                raw_evidence = self.evidence.list_for_case(case_id)
                raw_conflicts = self.conflicts.list_for_case(case_id)
                mappings = UnitMappingRepository(self.session).list_for_case(case_id)
                decs = [{"evidence_id": m.target_evidence_id, "unit_id": m.unit_id, "pairings": m.chosen_pairings} for m in mappings]
                units = compile_reporting_units(case_id, raw_facts, raw_evidence, decs if decs else None)
                units_report = assess_units(case_id=case_id, units=units, facts=raw_facts, evidence=raw_evidence, conflicts=raw_conflicts, route=case.route)
                base["readiness_units"] = units_report
            except Exception:
                base["readiness_units"] = None
        return base

    def _zip_bytes(self, case_id: str, artifacts: list[ArtifactRecord]) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for artifact in artifacts:
                name = str(artifact.verify_details.get("filename", artifact.artifact_id))
                data = self.storage.read_bytes(case_id, artifact.storage_key)
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                archive.writestr(info, data)
        return buffer.getvalue()

    def _manifest_name(self, type_name: str) -> str:
        mapping = {
            "VERIFICATION_BRIEF": "verification_brief.pdf",
            "ACTION_PLAN": "action_plan.pdf",
            "EVIDENCE_PACK": "evidence_pack.pdf",
            "READINESS_REPORT": "readiness_report.pdf",
            "BANK_HANDOFF_PACK": "bank_handoff_pack.pdf",
            "IASC_HANDOFF_PACK": "iasc_handoff_pack.pdf",
            "POLICE_HANDOFF_PACK": "police_handoff_pack.pdf",
            "REPORTING_UNIT_JSON": "unit.json",
            "UNIT_BANK_PACK": "bank_handoff_pack.pdf",
            "UNIT_IASC_PACK": "iasc_handoff_pack.pdf",
            "CASE_JSON": "case.json",
            "CHECKLIST": "handoff.md",
            "MANIFEST": "manifest.sha256",
        }
        return mapping.get(type_name, type_name.lower())


    def _assert_safe_copy(self, text: str) -> None:
        if contains_absolute_copy(text):
            raise ArtifactVerifyFailed("salinan mutlak tidak diizinkan")

    def _readiness(self, case_id: str) -> dict[str, Any]:
        case = self.cases.get(case_id)
        return assess(
            case_id=case_id,
            route=case.route,
            facts=self.facts.list_for_case(case_id),
            conflicts=self.conflicts.list_for_case(case_id),
            evidence=self.evidence.list_for_case(case_id),
            transactions=self.transactions.list_for_case(case_id),
        )

    def _safety_header(self, case_id: str, snapshot_hash: str, profile_version: str, incomplete: bool) -> list[str]:
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED",
            "Tanggap60 tidak mengirim laporan dan tidak menjamin dana kembali atau penerimaan laporan.",
            f"Profile kesiapan {profile_version}",
        ]
        if incomplete:
            lines.insert(2, "BELUM LENGKAP — PERLU TINDAKAN")
        return lines

    def _readiness_lines(self, case_id: str, snapshot_hash: str, report: dict[str, Any]) -> list[str]:
        lines = self._safety_header(case_id, snapshot_hash, str(report["profile_version"]), report["overall_status"] != "READY")
        lines.append("## Ringkasan kesiapan")
        for channel in report["channels"]:
            lines.append(f"## {channel['label']}")
            lines.append(f"{channel['status_label']} ({channel['checks_met']}/{channel['checks_total']})")
            for check in channel["checks"]:
                lines.append(f"- {check['label']}: {soften(check['reason'])}")
        lines.append(str(report["disclaimer"]))
        return lines

    def _channel_pack_lines(
        self,
        case_id: str,
        snapshot_hash: str,
        report: dict[str, Any],
        channel: str,
    ) -> list[str]:
        block = next(item for item in report["channels"] if item["channel"] == channel)
        incomplete = block["status"] != "READY"
        lines = self._safety_header(case_id, snapshot_hash, str(report["profile_version"]), incomplete)
        lines.append(f"Paket untuk {block['label']}.")
        lines.append("## Ringkasan kejadian")
        facts = [f for f in self.facts.list_for_case(case_id) if f.review_status != ReviewStatus.CANDIDATE]
        if facts:
            lines.extend(f"- {_fact_line(f)}" for f in facts)
        else:
            lines.append("- Belum ada fakta yang dikunci.")
        if any(c["check_id"].endswith("CHRONOLOGY") and c["status"] == "MET" for c in block["checks"]):
            lines.append("Kronologi disusun dari waktu, klaim, dan transaksi yang sudah ditinjau.")
        lines.append("## Daftar bukti")
        evidence = self.evidence.list_for_case(case_id)
        if evidence:
            lines.extend(f"- {item.original_name_display}" for item in evidence)
        else:
            lines.append("- Belum ada berkas.")
        gaps = [c for c in block["checks"] if c["status"] in {"MISSING", "CONFLICT"}]
        lines.append("## Yang masih kurang")
        if gaps:
            for check in gaps:
                lines.append(f"- {check['label']}: {soften(check['action'] or check['reason'])}")
        else:
            lines.append("- Tidak ada kekurangan wajib pada pemeriksaan internal.")
        lines.append("Buka kanal resmi sendiri. Tanggap60 tidak mengirim laporan.")
        lines.append(str(report["disclaimer"]))
        return lines

    def _plan_lines_v2(self, case_id: str, snapshot_hash: str, next_action: dict[str, Any] | None, units: list) -> list[str]:
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED",
            "SatuAman membantu menyusun langkah. Tidak mengirim laporan.",
        ]
        if next_action:
            lines.append("## Lakukan sekarang")
            label = soften(next_action.get("label"))
            reason = soften(next_action.get("reason"))
            lines.append(f"- {label}" + (f" — {reason}" if reason else ""))
        lines.append("## Transaksi")
        if not units:
            lines.append("- Belum ada transaksi terpasang.")
        for index, unit in enumerate(units, start=1):
            status = getattr(unit, "mapping_status", "UNKNOWN")
            status_str = status.value if hasattr(status, "value") else str(status)
            if status_str == "AMBIGUOUS":
                lines.append(f"- Transaksi {index}: rekening, nominal, atau waktu masih lebih dari satu kemungkinan.")
            else:
                dest = unit.destination_account or "belum ada"
                lines.append(f"- Ke {dest} · {_money(unit.amount)} · {unit.transferred_at or 'waktu belum ada'}")
        return lines

    def _readiness_lines_v2(self, case_id: str, snapshot_hash: str, report: dict[str, Any] | None) -> list[str]:
        if not report:
            return self._readiness_lines(case_id, snapshot_hash, self._readiness(case_id))
        profile_version = str(report.get("profile_version") or "unknown")
        overall = report.get("overall_status") or "NEEDS_ACTION"
        incomplete = overall != "READY"
        lines = self._safety_header(case_id, snapshot_hash, profile_version, incomplete)
        lines.append(f"## Ringkasan kesiapan — {human(overall, 'channel')}")
        for index, unit_rep in enumerate(report.get("units", []), start=1):
            lines.append(f"## Transaksi {index}")
            lines.append(human(unit_rep.get("overall_status"), "channel"))
            for ch in unit_rep["channels"]:
                lines.append(f"- {human(ch['channel'], 'channel')}: {ch['status_label']} ({ch['checks_met']}/{ch['checks_total']})")
                for ck in ch["checks"]:
                    if ck["status"] in {"MISSING", "CONFLICT", "PREPARE_EXTERNALLY"}:
                        lines.append(f"- {ck['label']}: {soften(ck['reason'])}")
        incident = report.get("incident_police")
        if incident:
            lines.append("## Polisi")
            lines.append(f"{incident['status_label']} ({incident['checks_met']}/{incident['checks_total']})")
        lines.append(str(report.get("disclaimer") or ""))
        return lines

    def _unit_pack_lines(self, case_id: str, snapshot_hash: str, unit, urep: dict[str, Any] | None, channel: str, is_ready: bool | None) -> list[str]:
        profile_version = ""
        try:
            from app.services.readiness import load_profile

            profile_version = load_profile()["profile_version"]
        except Exception:
            profile_version = "unknown"
        incomplete = not is_ready
        channel_label = "bank" if channel == "BANK" else "IASC"
        lines = self._safety_header(case_id, snapshot_hash, profile_version, incomplete)
        lines.append(f"Paket untuk {channel_label}.")
        lines.append("## Ringkasan transaksi")
        lines.append(f"- Rekening tujuan: {unit.destination_account or 'Belum dipilih'}")
        lines.append(f"- Nominal: {_money(unit.amount) if unit.amount is not None else 'Belum dipilih'}")
        lines.append(f"- Waktu: {unit.transferred_at or 'Belum dipilih'}")
        status = getattr(unit.mapping_status, "value", str(unit.mapping_status))
        if status == "AMBIGUOUS":
            lines.append("- Pasangan rekening, nominal, dan waktu belum dipilih di tinjauan.")
        names = {item.evidence_id: item.original_name_display for item in self.evidence.list_for_case(case_id)}
        lines.append("## Bukti")
        shown = False
        for eid in unit.evidence_ids:
            label = names.get(eid)
            if label:
                lines.append(f"- {label}")
                shown = True
        if not shown:
            lines.append("- Lihat paket bukti.")
        lines.append("## Fakta")
        facts = {f.fact_id: f for f in self.facts.list_for_case(case_id)}
        any_fact = False
        for fid in unit.fact_ids:
            fact = facts.get(fid)
            if fact:
                lines.append(f"- {_fact_line(fact)}")
                any_fact = True
        if not any_fact:
            lines.append("- Belum ada fakta terpasang.")
        if urep:
            lines.append("## Kesiapan kanal")
            for ch in urep["channels"]:
                match_bank = channel == "BANK" and ch["channel"] == "BANK_PJP"
                match_iasc = channel == "IASC" and ch["channel"] == "IASC"
                if match_bank or match_iasc or channel.lower() in ch["channel"].lower():
                    lines.append(f"- {human(ch['channel'], 'channel')}: {ch['status_label']}")
                    for ck in ch["checks"]:
                        if ck["status"] in {"MISSING", "CONFLICT", "PREPARE_EXTERNALLY"}:
                            lines.append(f"- {ck['label']}: {soften(ck['reason'] or ck['action'])}")
        lines.append("Buka kanal resmi sendiri. Tanggap60 tidak mengirim laporan.")
        return lines

    def _unit_json_data(self, unit, report: dict[str, Any] | None) -> dict[str, Any]:
        urep = None
        if report:
            urep = next((r for r in report.get("units", []) if r["unit_id"] == unit.unit_id), None)
        return {
            "unit_id": unit.unit_id,
            "case_id": unit.case_id,
            "source_account": unit.source_account,
            "destination_account": unit.destination_account,
            "amount": unit.amount,
            "currency": "IDR",
            "transferred_at": transaction_time_or_none(unit.transferred_at) if unit.transferred_at else None,
            "fact_ids": list(unit.fact_ids),
            "evidence_ids": list(unit.evidence_ids),
            "mapping_status": getattr(unit.mapping_status, "value", str(unit.mapping_status)),
            "mapping_reason": unit.mapping_reason,
            "mapping_provenance": unit.mapping_provenance,
            "readiness": urep,
        }


def _fact_line(fact: Any) -> str:
    return f"{human(fact.type.value, 'fact')}: {fact.raw_value} ({human(fact.review_status.value, 'review')})"


def _money(value: object) -> str:
    if value is None:
        return "Belum ada"
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value)
    return f"Rp{number:,}".replace(",", ".")


def payload_notice() -> str:
    from app.config import NOTICE_VERSION

    return NOTICE_VERSION


def transaction_time_or_none(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or "T" not in text:
        return None
    return text


def _json_num(value: str | None) -> float | str | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return value
