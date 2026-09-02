from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.errors import ValidationFailed
from app.infrastructure.storage import CaseStorage


def test_traversal_blocked(tmp_path: Path) -> None:
    store = CaseStorage(tmp_path / "root", 10_000_000)
    store.ensure_case_dir("case-a")
    with pytest.raises(ValidationFailed):
        store.path_for("case-a", "../escape")
    with pytest.raises(ValidationFailed):
        store.case_dir("..")


def test_atomic_write(tmp_path: Path) -> None:
    store = CaseStorage(tmp_path / "root", 10_000_000)
    path = store.write_atomic("case-a", "abc", b"hello")
    assert path.read_bytes() == b"hello"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
