from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, sqlite_file_from_url


def test_sqlite_absolute() -> None:
    assert sqlite_file_from_url("sqlite:////var/lib/tanggap60/db/x.db") == Path(
        "/var/lib/tanggap60/db/x.db"
    )


def test_missing_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="", case_storage_dir=Path("/tmp/cases"))  # type: ignore[call-arg]


def test_relative_storage_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="xxxxxxxxxxxxxxxx", case_storage_dir=Path("relative"))  # type: ignore[arg-type]


def test_bad_iasc() -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            secret_key="xxxxxxxxxxxxxxxx",
            case_storage_dir=Path("/tmp/cases"),
            official_iasc_url="https://evil.example",
        )
