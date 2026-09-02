from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import jsonschema
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.domain.errors import ArtifactVerifyFailed
from app.domain.models import ArtifactType, VerifyStatus
from app.domain.policies import contains_absolute_copy, sha256_bytes
from app.infrastructure.repositories import ArtifactRepository
from app.infrastructure.storage import CaseStorage

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json"
POST_ZIP_NAMES = frozenset(
    {
        "action_plan.pdf",
        "evidence_pack.pdf",
        "readiness_report.pdf",
        "bank_handoff_pack.pdf",
        "iasc_handoff_pack.pdf",
        "police_handoff_pack.pdf",
        "case.json",
        "handoff.md",
        "manifest.sha256",
    }
)
CHANNEL_PACKS = {
    ArtifactType.READINESS_REPORT,
    ArtifactType.BANK_HANDOFF_PACK,
    ArtifactType.IASC_HANDOFF_PACK,
    ArtifactType.POLICE_HANDOFF_PACK,
}
CHANNEL_BY_TYPE = {
    ArtifactType.BANK_HANDOFF_PACK: "BANK_PJP",
    ArtifactType.IASC_HANDOFF_PACK: "IASC",
    ArtifactType.POLICE_HANDOFF_PACK: "POLICE",
}


class VerifierService:
    def __init__(self, session: Session, storage: CaseStorage) -> None:
        self.artifacts = ArtifactRepository(session)
        self.storage = storage
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def verify_case(self, case_id: str, snapshot_hash: str) -> list[dict[str, object]]:
        artifacts = self.artifacts.list_for_case(case_id)
        if not artifacts:
            raise ArtifactVerifyFailed("tidak ada artefak")
        results: list[dict[str, object]] = []
        by_type = {a.type: a for a in artifacts}
        json_art = by_type.get(ArtifactType.CASE_JSON)
        if json_art is None:
            raise ArtifactVerifyFailed("case.json hilang")
        raw = self.storage.read_bytes(case_id, json_art.storage_key)
        if sha256_bytes(raw) != json_art.sha256:
            json_art.verify_status = VerifyStatus.FAIL
            json_art.verify_details = {"error": "hash mismatch"}
            self.artifacts.save(json_art)
            raise ArtifactVerifyFailed("hash JSON tidak cocok")
        payload = json.loads(raw.decode("utf-8"))
        try:
            jsonschema.validate(payload, self.schema)
            json_ok = True
            json_error = None
        except jsonschema.ValidationError as exc:
            json_ok = False
            json_error = exc.message
        json_art.verify_status = VerifyStatus.PASS if json_ok else VerifyStatus.FAIL
        json_art.verify_details = {"schema": "pass" if json_ok else json_error}
        self.artifacts.save(json_art)
        results.append({"type": "CASE_JSON", "status": json_art.verify_status.value})
        if not json_ok:
            raise ArtifactVerifyFailed("JSON schema gagal")
        if payload.get("schema_version") == "2.1" and payload.get("route") == "POST_INCIDENT_RESPONSE":
            if not isinstance(payload.get("readiness"), dict):
                raise ArtifactVerifyFailed("readiness 2.1 hilang")
        manifest_map = self._manifest_map(case_id, by_type.get(ArtifactType.MANIFEST))
        for artifact in artifacts:
            data = self.storage.read_bytes(case_id, artifact.storage_key)
            checks: dict[str, object] = {}
            ok = True
            if sha256_bytes(data) != artifact.sha256:
                ok = False
                checks["hash"] = "fail"
            else:
                checks["hash"] = "pass"
            if artifact.source_snapshot_hash != snapshot_hash:
                ok = False
                checks["snapshot"] = "fail"
            else:
                checks["snapshot"] = "pass"
            if artifact.mime == "application/pdf":
                reader = PdfReader(BytesIO(data))
                checks["pages"] = len(reader.pages)
                if len(reader.pages) < 1:
                    ok = False
                pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if contains_absolute_copy(pdf_text):
                    ok = False
                    checks["copy"] = "fail"
                if artifact.type in CHANNEL_PACKS:
                    if "DRAF PENGGUNA" not in pdf_text or "NOT_VERIFIED" not in pdf_text:
                        ok = False
                        checks["safety_label"] = "fail"
                    if "profile" not in pdf_text.lower() and "Profile" not in pdf_text:
                        ok = False
                        checks["profile"] = "fail"
                    if _channel_incomplete(artifact.type, payload) and "BELUM LENGKAP" not in pdf_text:
                        ok = False
                        checks["incomplete_label"] = "fail"
            if artifact.type in {ArtifactType.CASE_JSON, ArtifactType.CHECKLIST, ArtifactType.MANIFEST}:
                if contains_absolute_copy(data.decode("utf-8", errors="replace")):
                    ok = False
                    checks["copy"] = "fail"
            if artifact.type == ArtifactType.MANIFEST:
                checks["entries"] = len(manifest_map)
                if not manifest_map:
                    ok = False
            if artifact.type == ArtifactType.CASE_ZIP:
                ok = self._verify_zip(data, manifest_map, checks) and ok
            artifact.verify_status = VerifyStatus.PASS if ok else VerifyStatus.FAIL
            artifact.verify_details = {**artifact.verify_details, **checks}
            self.artifacts.save(artifact)
            results.append({"type": artifact.type.value, "status": artifact.verify_status.value})
            if not ok:
                raise ArtifactVerifyFailed(f"verifikasi {artifact.type.value} gagal")
        return results

    def _manifest_map(self, case_id: str, manifest) -> dict[str, str]:
        if manifest is None:
            return {}
        data = self.storage.read_bytes(case_id, manifest.storage_key)
        mapping: dict[str, str] = {}
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            mapping[parts[-1]] = parts[0]
        return mapping

    def _verify_zip(self, data: bytes, manifest_map: dict[str, str], checks: dict[str, object]) -> bool:
        ok = True
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                bad = archive.testzip()
                checks["zip"] = "fail" if bad else "pass"
                if bad:
                    return False
                names = set(archive.namelist())
                if "action_plan.pdf" in manifest_map:
                    for required in POST_ZIP_NAMES:
                        if required not in names:
                            ok = False
                            checks[f"missing:{required}"] = "fail"
                    unexpected = names - POST_ZIP_NAMES
                    if unexpected:
                        ok = False
                        checks["unexpected"] = "fail"
                for name in names:
                    payload = archive.read(name)
                    digest = sha256_bytes(payload)
                    if name == "manifest.sha256":
                        checks[name] = "pass"
                        continue
                    expected = manifest_map.get(name)
                    if expected != digest:
                        ok = False
                        checks[name] = "fail"
                    else:
                        checks[name] = "pass"
                for name in manifest_map:
                    if name not in names:
                        ok = False
                        checks[f"missing:{name}"] = "fail"
        except zipfile.BadZipFile:
            ok = False
            checks["zip"] = "fail"
        return ok


def _channel_incomplete(artifact_type: ArtifactType, payload: dict[str, object]) -> bool:
    report = payload.get("readiness")
    if not isinstance(report, dict):
        return False
    if artifact_type == ArtifactType.READINESS_REPORT:
        return str(report.get("overall_status") or "") != "READY"
    channel = CHANNEL_BY_TYPE.get(artifact_type)
    if channel is None:
        return False
    blocks = report.get("channels")
    if not isinstance(blocks, list):
        return False
    block = next((item for item in blocks if isinstance(item, dict) and item.get("channel") == channel), None)
    if not isinstance(block, dict):
        return False
    return str(block.get("status") or "") != "READY"
