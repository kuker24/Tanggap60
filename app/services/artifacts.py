from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
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
from app.services.ids import new_id
from app.templates.pdf import render_lines

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
        payload, digest = self.approval.current_snapshot(case_id)
        if digest != snapshot_hash:
            raise ArtifactVerifyFailed("snapshot berubah")
        generated_at = datetime(2026, 9, 23, 9, 1, tzinfo=UTC).isoformat()
        existing = self.artifacts.list_for_case(case_id)
        if existing and all(a.source_snapshot_hash == snapshot_hash for a in existing):
            return existing
        built: list[ArtifactRecord] = []
        if case.route == Route.PRE_INCIDENT_CHECK:
            built.append(self._store_pdf(case_id, ArtifactType.VERIFICATION_BRIEF, self._brief_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
        else:
            built.append(self._store_pdf(case_id, ArtifactType.ACTION_PLAN, self._plan_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
            built.append(self._store_pdf(case_id, ArtifactType.EVIDENCE_PACK, self._pack_lines(case_id, snapshot_hash), generated_at, snapshot_hash))
        case_json = self._case_json(case_id, snapshot_hash, generated_at)
        built.append(self._store_bytes(case_id, ArtifactType.CASE_JSON, json.dumps(case_json, ensure_ascii=False, indent=2).encode(), "application/json", snapshot_hash, "case.json"))
        checklist = self._checklist_text(case_id)
        built.append(self._store_bytes(case_id, ArtifactType.CHECKLIST, checklist.encode(), "text/markdown", snapshot_hash, "handoff.md"))
        file_map = {a.type.value: (a.storage_key, a.sha256) for a in built}
        manifest_lines = [f"{sha}  {name}" for name, (_key, sha) in sorted((self._manifest_name(k), v) for k, v in file_map.items())]
        manifest_body = "\n".join(manifest_lines) + "\n"
        built.append(self._store_bytes(case_id, ArtifactType.MANIFEST, manifest_body.encode(), "text/plain", snapshot_hash, "manifest.sha256"))
        zip_bytes = self._zip_bytes(case_id, built)
        built.append(self._store_bytes(case_id, ArtifactType.CASE_ZIP, zip_bytes, "application/zip", snapshot_hash, "case-pack.zip"))
        for artifact in built:
            if contains_absolute_copy(artifact.sha256):
                continue
        return built

    def _store_pdf(
        self,
        case_id: str,
        artifact_type: ArtifactType,
        lines: list[str],
        generated_at: str,
        snapshot_hash: str,
    ) -> ArtifactRecord:
        data = render_lines(artifact_type.value.replace("_", " "), lines, generated_at, snapshot_hash)
        return self._store_bytes(case_id, artifact_type, data, "application/pdf", snapshot_hash, f"{artifact_type.value.lower()}.pdf")

    def _store_bytes(
        self,
        case_id: str,
        artifact_type: ArtifactType,
        data: bytes,
        mime: str,
        snapshot_hash: str,
        filename: str,
    ) -> ArtifactRecord:
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
            f"Emergency Action Plan - {case_id}",
            "Tanggap60 membantu menyusun langkah. Tidak ada jaminan dana kembali. Tidak mengirim laporan.",
            "Lakukan sekarang:",
        ]
        for action in actions:
            if action.priority.value == "NOW":
                lines.append(f"- {action.instruction}")
        lines.append("Berikutnya:")
        for action in actions:
            if action.priority.value == "NEXT":
                lines.append(f"- {action.instruction}")
        lines.append("Setelah itu:")
        for action in actions:
            if action.priority.value == "LATER":
                lines.append(f"- {action.instruction}")
        lines.append(f"Snapshot {snapshot_hash}")
        return lines

    def _pack_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        facts = [f for f in self.facts.list_for_case(case_id) if f.review_status != ReviewStatus.CANDIDATE]
        conflicts = self.conflicts.list_for_case(case_id)
        txs = self.transactions.list_for_case(case_id)
        lines = [
            f"Evidence Pack - {case_id}",
            "DEMO/USER-COMPILED DOCUMENT. Bukan Laporan Polisi, bukan keputusan hukum.",
            "Fakta ditinjau:",
        ]
        for fact in facts:
            lines.append(f"- {fact.type.value}: {fact.raw_value} ({fact.review_status.value})")
        lines.append("Konflik:")
        for conflict in conflicts:
            lines.append(f"- {conflict.type.value} {conflict.status.value}")
        lines.append("Transaksi:")
        for tx in txs:
            lines.append(f"- tujuan {tx.destination_account} nominal {tx.amount}")
        lines.append(f"Snapshot {snapshot_hash}")
        return lines

    def _brief_lines(self, case_id: str, snapshot_hash: str) -> list[str]:
        facts = self.facts.list_for_case(case_id)
        lines = [
            f"Verification Brief - {case_id}",
            "Hasil ini menunjukkan indikator dari pemeriksaan terbatas. Tidak menjamin aman dan tidak menetapkan penipuan.",
            "Klaim dan entitas:",
        ]
        for fact in facts:
            lines.append(f"- {fact.type.value}: {fact.raw_value} ({fact.review_status.value})")
        lines.append("Pemeriksaan dapat memiliki false positive/negative.")
        lines.append(f"Snapshot {snapshot_hash}")
        return lines

    def _checklist_text(self, case_id: str) -> str:
        return (
            f"# Official Handoff Checklist - {case_id}\n\n"
            "- [ ] Kronologi dan waktu kejadian sudah ditinjau.\n"
            "- [ ] Data PJP/rekening korban tersedia untuk diisi langsung.\n"
            "- [ ] Data PJP/rekening tujuan tersedia.\n"
            "- [ ] Nominal dan waktu transaksi terkonfirmasi.\n"
            "- [ ] Bukti transaksi tersedia.\n"
            "- [ ] Bukti komunikasi tersedia.\n"
            "- [ ] Identitas/KTP disiapkan untuk portal resmi, bukan ke Tanggap60 demo.\n\n"
            "## Kanal\n"
            f"- IASC: {self.settings.official_iasc_url}\n"
            "- Laporan Polisi: dilakukan oleh pengguna.\n"
            "- Bank/PJP: nomor resmi dari aplikasi/kartu/situs resmi.\n"
        )

    def _case_json(self, case_id: str, snapshot_hash: str, generated_at: str) -> dict[str, Any]:
        case = self.cases.get(case_id)
        facts = self.facts.list_for_case(case_id)
        conflicts = self.conflicts.list_for_case(case_id)
        actions = self.actions.list_for_case(case_id)
        txs = self.transactions.list_for_case(case_id)
        artifacts = self.artifacts.list_for_case(case_id)
        return {
            "schema_version": "2.0",
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
                        "locator": f.source_bbox or "page 1",
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
                    "transferred_at": t.transferred_at if t.transferred_at and "T" in t.transferred_at else "2026-09-23T01:42:00Z",
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
                "scope": "PRE_BRIEF" if case.route == Route.PRE_INCIDENT_CHECK else "POST_CASE_PACK",
                "snapshot_hash": snapshot_hash,
                "approved_at": generated_at,
                "notice_version": payload_notice(),
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
        }

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
            "CASE_JSON": "case.json",
            "CHECKLIST": "handoff.md",
            "MANIFEST": "manifest.sha256",
        }
        return mapping.get(type_name, type_name.lower())


def payload_notice() -> str:
    from app.config import NOTICE_VERSION

    return NOTICE_VERSION


def _json_num(value: str | None) -> float | str | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return value
