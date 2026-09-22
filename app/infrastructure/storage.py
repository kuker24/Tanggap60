from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from app.domain.errors import ResourceLimit, ValidationFailed


class CaseStorage:
    def __init__(self, root: Path, quota_bytes: int) -> None:
        self.root = root.resolve()
        self.quota_bytes = quota_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- owner-only directory

    def case_dir(self, case_id: str) -> Path:
        if "/" in case_id or ".." in case_id or case_id.startswith("."):
            raise ValidationFailed("invalid case id")
        path = (self.root / case_id).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValidationFailed("storage path escape")
        return path

    def ensure_case_dir(self, case_id: str) -> Path:
        path = self.case_dir(case_id)
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- owner-only directory
        return path

    def new_key(self) -> str:
        return secrets.token_hex(16)

    def path_for(self, case_id: str, storage_key: str) -> Path:
        if "/" in storage_key or ".." in storage_key:
            raise ValidationFailed("invalid storage key")
        path = (self.case_dir(case_id) / storage_key).resolve()
        if not str(path).startswith(str(self.case_dir(case_id))):
            raise ValidationFailed("storage path escape")
        return path

    def write_atomic(self, case_id: str, storage_key: str, data: bytes) -> Path:
        self._enforce_quota(len(data))
        directory = self.ensure_case_dir(case_id)
        dest = self.path_for(case_id, storage_key)
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, dest)
            os.chmod(dest, 0o600)
        except Exception:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        return dest

    def write_stream(self, case_id: str, storage_key: str, chunks: object, max_bytes: int) -> tuple[Path, int]:
        directory = self.ensure_case_dir(case_id)
        dest = self.path_for(case_id, storage_key)
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:  # type: ignore[not-an-iterable]
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ResourceLimit("upload exceeds limit")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, dest)
            os.chmod(dest, 0o600)
        except Exception:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        self._enforce_quota(0)
        return dest, size

    def read_bytes(self, case_id: str, storage_key: str) -> bytes:
        return self.path_for(case_id, storage_key).read_bytes()

    def delete_key(self, case_id: str, storage_key: str) -> None:
        path = self.path_for(case_id, storage_key)
        if path.exists():
            path.unlink()

    def purge_case(self, case_id: str) -> None:
        directory = self.case_dir(case_id)
        if not directory.exists():
            return
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()
        directory.rmdir()

    def used_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def reconcile_temp(self) -> int:
        removed = 0
        for path in self.root.rglob(".tmp-*"):
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    def _enforce_quota(self, incoming: int) -> None:
        if self.used_bytes() + incoming > self.quota_bytes:
            raise ResourceLimit("case storage quota exceeded")
