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
from app.domain.policies import sha256_bytes
from app.infrastructure.repositories import ArtifactRepository
from app.infrastructure.storage import CaseStorage

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json"


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
            if artifact.type == ArtifactType.CASE_ZIP:
                try:
                    with zipfile.ZipFile(BytesIO(data)) as archive:
                        bad = archive.testzip()
                        checks["zip"] = "fail" if bad else "pass"
                        if bad:
                            ok = False
                except zipfile.BadZipFile:
                    ok = False
                    checks["zip"] = "fail"
            if artifact.type == ArtifactType.MANIFEST:
                checks["size"] = len(data)
            artifact.verify_status = VerifyStatus.PASS if ok else VerifyStatus.FAIL
            artifact.verify_details = {**artifact.verify_details, **checks}
            self.artifacts.save(artifact)
            results.append({"type": artifact.type.value, "status": artifact.verify_status.value})
            if not ok:
                raise ArtifactVerifyFailed(f"verifikasi {artifact.type.value} gagal")
        return results
