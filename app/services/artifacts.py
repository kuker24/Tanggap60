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
        approved_target_id: str | None = None
        try:
            payload, digest = self.approval.current_snapshot(case_id)
            if digest != snapshot_hash:
                from app.infrastructure.repositories import ApprovalRepository

                all_ap = ApprovalRepository(self.session).list_for_case(case_id)
                matched = next((a for a in all_ap if a.snapshot_hash == snapshot_hash and a.revoked_at is None), None)
                if matched and matched.target_id:
                    approved_target_id = matched.target_id
                    payload, digest = self.approval.unit_snapshot(case_id, matched.target_id)
                if digest != snapshot_hash:
                    raise ArtifactVerifyFailed("snapshot berubah")
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
            except Exception as exc:
                raise ArtifactVerifyFailed("pemeriksaan unit gagal") from exc
        if case.route == Route.PRE_INCIDENT_CHECK:
            built.append(self._store_pdf(case_id, ArtifactType.VERIFICATION_BRIEF, self._brief_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
        elif use_units:
            # 2.2 path: per-unit packs
            report = self._readiness(case_id)
            emit_units = [u for u in units if u.unit_id == approved_target_id] if approved_target_id else units
            built.append(self._store_pdf(case_id, ArtifactType.ACTION_PLAN, self._plan_lines_v2(case_id, snapshot_hash, next_action_payload, emit_units), generated_at, snapshot_hash))
            built.append(self._store_pdf(case_id, ArtifactType.EVIDENCE_PACK, self._pack_lines(case_id, snapshot_hash, emit_units), generated_at, snapshot_hash))
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.READINESS_REPORT,
                    self._readiness_lines_v2(case_id, snapshot_hash, units_report),
                    generated_at,
                    snapshot_hash,
                )
            )
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.POLICE_HANDOFF_PACK,
                    self._police_lines(case_id, snapshot_hash, report, emit_units),
                    generated_at,
                    snapshot_hash,
                )
            )
            for unit in emit_units:
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
                if _mapping(unit) != "COMPLETE":
                    continue
                urep = None
                if units_report:
                    urep = next((r for r in units_report["units"] if r["unit_id"] == unit.unit_id), None)
                if _channel_ready(urep, "BANK_PJP"):
                    built.append(
                        self._store_pdf(
                            case_id,
                            ArtifactType.UNIT_BANK_PACK,
                            self._unit_pack_lines(case_id, snapshot_hash, unit, urep, "BANK", True),
                            generated_at,
                            snapshot_hash,
                            filename=f"units/{unit.unit_id}/bank_handoff_pack.pdf",
                        )
                    )
                if _channel_ready(urep, "IASC"):
                    built.append(
                        self._store_pdf(
                            case_id,
                            ArtifactType.UNIT_IASC_PACK,
                            self._unit_pack_lines(case_id, snapshot_hash, unit, urep, "IASC", True),
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
            ):
                block = next((item for item in report.get("channels") or [] if item.get("channel") == channel), None)
                if not block or block.get("status") != "READY":
                    continue
                built.append(
                    self._store_pdf(
                        case_id,
                        artifact_type,
                        self._channel_pack_lines(case_id, snapshot_hash, report, channel),
                        generated_at,
                        snapshot_hash,
                    )
                )
            built.append(
                self._store_pdf(
                    case_id,
                    ArtifactType.POLICE_HANDOFF_PACK,
                    self._police_lines(case_id, snapshot_hash, report, []),
                    generated_at,
                    snapshot_hash,
                )
            )
        case_json = self._case_json(case_id, snapshot_hash, generated_at)
        built.append(self._store_bytes(case_id, ArtifactType.CASE_JSON, json.dumps(case_json, ensure_ascii=False, indent=2).encode(), "application/json", snapshot_hash, "case.json"))
        checklist = self._checklist_text(case_id)
        built.append(self._store_bytes(case_id, ArtifactType.CHECKLIST, checklist.encode(), "text/markdown", snapshot_hash, "handoff.md"))
        file_map = {str(a.verify_details.get("filename", a.type.value.lower())): (a.storage_key, a.sha256) for a in built}
        for item in self.evidence.list_for_case(case_id):
            try:
                data = self.storage.read_bytes(case_id, item.storage_key)
            except Exception:
                continue
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in item.original_name_display) or item.evidence_id
            file_map[f"bukti/{item.evidence_id}-{safe}"] = (item.storage_key, sha256_bytes(data))
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
            "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi.",
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

    def _pack_lines(self, case_id: str, snapshot_hash: str, units: list | None = None) -> list[str]:
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi.",
            "Draf pengguna. Bukan laporan polisi dan bukan keputusan hukum.",
            "## Kronologi",
        ]
        story = _story_lines(units or [], self._locked_facts(case_id))
        lines.extend(story)
        lines.append("## Transaksi")
        complete = [u for u in (units or []) if _mapping(u) == "COMPLETE"]
        if complete:
            lines.extend(_tx_bullet(i, u) for i, u in enumerate(complete, start=1))
        else:
            txs = [t for t in self.transactions.list_for_case(case_id) if t.destination_account and t.destination_account != "AMBIGUOUS"]
            if txs:
                lines.extend(f"- Tujuan {tx.destination_account} · {_money(tx.amount)}" for tx in txs)
            else:
                lines.append("- Informasi ini belum tersedia.")
        return lines

    def _brief_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        facts = self.facts.list_for_case(case_id)
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi.",
            "Hasil ini menunjukkan indikator dari pemeriksaan terbatas. Tidak menjamin aman dan tidak menetapkan penipuan.",
            "## Klaim dan entitas",
        ]
        if facts:
            lines.extend(f"- {_fact_line(f)}" for f in facts)
        else:
            lines.append("- Belum ada data.")
        lines.append("Pemeriksaan dapat keliru. Cek ulang di situs resmi.")
        return lines

    def _checklist_text(self, case_id: str) -> str:
        report = self._readiness(case_id)
        seen: set[str] = set()
        rows: list[str] = []
        for channel in report.get("channels") or []:
            for check in channel.get("checks") or []:
                label = str(check.get("label") or "").strip()
                if not label or label in seen:
                    continue
                seen.add(label)
                status = str(check.get("status") or "")
                if status == "MET":
                    rows.append(f"- [x] {soften(label)}")
                elif status == "PREPARE_EXTERNALLY":
                    rows.append(f"- [ ] {soften(label)} (isi di situs resmi)")
                else:
                    rows.append(f"- [ ] {soften(label)}")
        if not rows:
            rows = [
                "- [ ] Kronologi dan waktu kejadian sudah ditinjau.",
                "- [ ] Data rekening tujuan tersedia.",
                "- [ ] Jumlah uang dan waktu transaksi terkonfirmasi.",
                "- [ ] Bukti transaksi tersedia.",
                "- [ ] Bukti komunikasi tersedia.",
                "- [ ] Identitas/KTP disiapkan untuk situs resmi, bukan dikirim ke SatuAman.",
            ]
        body = "\n".join(rows)
        return (
            "# Daftar periksa sebelum lapor\n\n"
            "Draf pengguna. Bukan dokumen resmi. Status resmi: NOT_VERIFIED.\n\n"
            f"{body}\n\n"
            "## Situs resmi\n"
            f"- IASC: {self.settings.official_iasc_url}\n"
            "- Laporan polisi: Anda yang mengirim.\n"
            "- Bank: nomor resmi dari aplikasi, kartu, atau situs resmi.\n"
        )

    def _locked_facts(self, case_id: str) -> list:
        return [
            f
            for f in self.facts.list_for_case(case_id)
            if f.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}
        ]

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
            if units and case.route == Route.POST_INCIDENT_RESPONSE:
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
        except ArtifactVerifyFailed:
            raise
        except Exception as exc:
            raise ArtifactVerifyFailed("case json unit gagal") from exc
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
            for item in self.evidence.list_for_case(case_id):
                safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in item.original_name_display) or item.evidence_id
                name = f"bukti/{item.evidence_id}-{safe}"
                try:
                    data = self.storage.read_bytes(case_id, item.storage_key)
                except Exception:
                    continue
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
            "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi.",
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
                lines.append(f"- {soften(check['label'])}: {soften(check['reason'])}")
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
        if channel == "IASC":
            lines.append("Lembar bantu pengisian IASC.")
            lines.append("## Rekening korban")
            lines.append("- Bank / nomor / nama: isi langsung di situs resmi.")
            lines.append("## Rekening terlapor")
            dests = [f for f in self._locked_facts(case_id) if f.type.value == "ACCOUNT" and "VICTIM" not in (f.raw_value or "")]
            amounts = [f for f in self._locked_facts(case_id) if f.type.value == "AMOUNT"]
            times = [f for f in self._locked_facts(case_id) if f.type.value == "DATETIME"]
            lines.append(f"- Nomor: {dests[0].raw_value if dests else 'Informasi ini belum tersedia.'}")
            lines.append("## Transaksi")
            lines.append(f"- Jumlah uang: {amounts[0].raw_value if amounts else 'Informasi ini belum tersedia.'}")
            lines.append(f"- Waktu: {times[0].raw_value if times else 'Informasi ini belum tersedia.'}")
        else:
            lines.append(f"Lembar lapor {block['label']}.")
            lines.append("## Ringkasan transaksi")
            lines.extend(_story_lines([], self._locked_facts(case_id)))
        lines.append("## Bukti yang disiapkan")
        evidence = self.evidence.list_for_case(case_id)
        if evidence:
            lines.extend(f"- {item.original_name_display}" for item in evidence)
        else:
            lines.append("- Belum ada berkas.")
        gaps = [c for c in block["checks"] if c["status"] in {"MISSING", "CONFLICT"}]
        lines.append("## Yang masih kurang")
        if gaps:
            for check in gaps:
                lines.append(f"- {soften(check['label'])}: {soften(check['action'] or check['reason'])}")
        else:
            lines.append("- Tidak ada kekurangan wajib pada cek kelengkapan.")
        lines.append("Buka situs resmi sendiri. Tanggap60 tidak mengirim laporan.")
        lines.append(str(report["disclaimer"]))
        return lines

    def _police_lines(self, case_id: str, snapshot_hash: str, report: dict[str, Any], units: list) -> list[str]:
        block = next(item for item in report["channels"] if item["channel"] == "POLICE")
        incomplete = block["status"] != "READY"
        lines = self._safety_header(case_id, snapshot_hash, str(report["profile_version"]), incomplete)
        lines.append("Ringkasan untuk laporan kepolisian.")
        lines.append("## Kronologi")
        lines.extend(_story_lines(units, self._locked_facts(case_id)))
        complete = [u for u in units if _mapping(u) == "COMPLETE" and u.amount is not None]
        lines.append("## Kerugian yang dikonfirmasi")
        if complete:
            lines.append(f"- {_money(sum(float(u.amount) for u in complete))}")
        else:
            amounts = [f for f in self._locked_facts(case_id) if f.type.value == "AMOUNT"]
            lines.append(f"- {amounts[0].raw_value}" if amounts else "- Informasi ini belum tersedia.")
        lines.append("## Daftar transaksi")
        if complete:
            lines.extend(_tx_bullet(i, u) for i, u in enumerate(complete, start=1))
        else:
            lines.append("- Pasangan transaksi belum dipilih.")
        lines.append("## Daftar bukti")
        evidence = self.evidence.list_for_case(case_id)
        if evidence:
            lines.extend(f"- {item.original_name_display}" for item in evidence)
        else:
            lines.append("- Belum ada berkas.")
        gaps = [c for c in block["checks"] if c["status"] in {"MISSING", "CONFLICT"}]
        if gaps:
            lines.append("## Yang masih kurang")
            for check in gaps:
                lines.append(f"- {soften(check['label'])}: {soften(check['action'] or check['reason'])}")
        lines.append("Buka situs resmi sendiri. Tanggap60 tidak mengirim laporan.")
        lines.append(str(report["disclaimer"]))
        return lines

    def _plan_lines_v2(self, case_id: str, snapshot_hash: str, next_action: dict[str, Any] | None, units: list) -> list[str]:
        lines = [
            "DRAF PENGGUNA — BUKAN DOKUMEN RESMI",
            "STATUS RESMI: NOT_VERIFIED. Belum diverifikasi oleh situs resmi.",
            "SatuAman membantu menyusun langkah. Tidak mengirim laporan.",
            "## Lakukan sekarang",
        ]
        complete = [u for u in units if _mapping(u) == "COMPLETE"]
        pending = [u for u in units if _mapping(u) != "COMPLETE"]
        if complete:
            for index, unit in enumerate(complete, start=1):
                lines.append(
                    f"- Hubungi bank. Transaksi {_money(unit.amount)} ke {unit.destination_account or 'rekening tujuan'} {unit.transferred_at or ''}.".replace(" .", ".")
                )
            lines.append("- Buka situs resmi IASC. Gunakan lembar IASC untuk transaksi yang sudah terpasang.")
        elif next_action:
            label = soften(next_action.get("label"))
            reason = soften(next_action.get("reason"))
            lines.append(f"- {label}" + (f" — {reason}" if reason else ""))
        else:
            lines.append("- Lengkapi data di tinjauan sebelum membawa paket ke situs resmi.")
        if pending:
            lines.append("## Yang masih kurang")
            for unit in pending:
                status = _mapping(unit)
                if status == "AMBIGUOUS":
                    lines.append("- Beberapa kemungkinan transaksi belum dipasangkan. Jangan gunakan paket bank sebelum dipilih.")
                else:
                    lines.append("- Transaksi belum lengkap (rekening, nominal, atau waktu kurang).")
        elif complete:
            lines.append("## Transaksi")
            lines.extend(_tx_bullet(i, u) for i, u in enumerate(complete, start=1))
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
                        lines.append(f"- {soften(ck['label'])}: {soften(ck['reason'])}")
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
        lines = self._safety_header(case_id, snapshot_hash, profile_version, incomplete)
        if channel == "IASC":
            lines.append("Lembar bantu pengisian IASC.")
            lines.append("## Rekening korban")
            lines.append("- Bank / nomor / nama: isi langsung di situs resmi.")
            lines.append("## Rekening terlapor")
            lines.append(f"- Nomor: {unit.destination_account or 'Informasi ini belum tersedia.'}")
            lines.append("## Transaksi")
            lines.append(f"- Jumlah uang: {_money(unit.amount) if unit.amount is not None else 'Informasi ini belum tersedia.'}")
            lines.append(f"- Tanggal / waktu: {unit.transferred_at or 'Informasi ini belum tersedia.'}")
            lines.append("## Kronologi")
            lines.extend(_story_lines([unit], self._locked_facts(case_id)))
        else:
            lines.append("Lembar lapor bank.")
            lines.append("## Ringkasan transaksi")
            lines.append(f"- Rekening tujuan: {unit.destination_account or 'Informasi ini belum tersedia.'}")
            lines.append(f"- Jumlah uang: {_money(unit.amount) if unit.amount is not None else 'Informasi ini belum tersedia.'}")
            lines.append(f"- Waktu: {unit.transferred_at or 'Informasi ini belum tersedia.'}")
            lines.append("## Yang dilakukan sekarang")
            lines.append("- Hubungi bank lewat nomor resmi di aplikasi, kartu, atau situs. Sampaikan transaksi di atas.")
        names = {item.evidence_id: item.original_name_display for item in self.evidence.list_for_case(case_id)}
        lines.append("## Bukti yang disiapkan")
        shown = False
        for eid in unit.evidence_ids:
            label = names.get(eid)
            if label:
                lines.append(f"- {label}")
                shown = True
        if not shown:
            leftovers = list(self.evidence.list_for_case(case_id)[:6])
            if leftovers:
                lines.extend(f"- {item.original_name_display}" for item in leftovers)
            else:
                lines.append("- Lihat paket bukti.")
        lines.append("Buka situs resmi sendiri. Tanggap60 tidak mengirim laporan.")
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


def _mapping(unit: Any) -> str:
    raw = getattr(unit, "mapping_status", "")
    return str(getattr(raw, "value", raw))


def _channel_ready(urep: dict[str, Any] | None, channel: str) -> bool:
    if not urep:
        return False
    return any(ch.get("channel") == channel and ch.get("status") == "READY" for ch in urep.get("channels") or [])


def _tx_bullet(index: int, unit: Any) -> str:
    dest = unit.destination_account or "rekening tujuan belum ada"
    when = unit.transferred_at or "waktu belum ada"
    return f"- Transaksi {index}: {_money(unit.amount)} ke {dest} · {when}"


def _story_lines(units: list, facts: list) -> list[str]:
    complete = [u for u in units if _mapping(u) == "COMPLETE"]
    if complete:
        lines = [_tx_bullet(i, u) for i, u in enumerate(complete, start=1)]
        claims = [f.raw_value for f in facts if getattr(f.type, "value", f.type) == "CLAIM"]
        if claims:
            lines.append(f"- Dari percakapan: {claims[0]}")
        return lines
    times = [f.raw_value for f in facts if getattr(f.type, "value", f.type) == "DATETIME"]
    amounts = [f.raw_value for f in facts if getattr(f.type, "value", f.type) == "AMOUNT"]
    dests = [
        f.raw_value
        for f in facts
        if getattr(f.type, "value", f.type) == "ACCOUNT" and "VICTIM" not in (f.raw_value or "")
    ]
    if times and amounts and dests and len(dests) == 1 and len(amounts) == 1:
        return [f"- Pada {times[0]} ada transfer {amounts[0]} ke {dests[0]}."]
    if times or amounts or dests:
        bits = []
        if times:
            bits.append(f"waktu {times[0]}")
        if amounts:
            bits.append(f"nominal {amounts[0]}")
        if dests:
            bits.append(f"rekening {dests[0]}")
        return [f"- Data yang sudah dikunci: {', '.join(bits)}. Pasangan lengkap belum dipilih."]
    return ["- Informasi ini belum tersedia."]


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
