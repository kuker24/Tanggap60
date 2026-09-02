from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NOTICE_VERSION = "2026-09-02-v2.0"
TEMPLATE_VERSION = "2.0.0"
TOOL_VERSION = "2.0.0"
DEFAULT_IASC_URL = "https://iasc.ojk.go.id/"
HANDOFF_ALLOWLIST = frozenset(
    {
        "https://iasc.ojk.go.id/",
        "https://iasc.ojk.go.id",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "competition"
    base_url: str = "http://127.0.0.1:8000"
    secret_key: str = Field(min_length=16)
    database_url: str = "sqlite:////var/lib/tanggap60/db/tanggap60.db"
    case_storage_dir: Path = Path("/var/lib/tanggap60/cases")
    case_ttl_seconds: int = 86400
    max_upload_bytes: int = 25 * 1024 * 1024
    max_upload_files: int = 8
    max_pdf_pages: int = 20
    max_image_pixels: int = 20_000_000
    case_storage_quota_bytes: int = 500 * 1024 * 1024
    hermes_endpoint: str | None = None
    model_api_key: str | None = None
    model_base_url: str | None = None
    model_name: str = "gpt-4o-mini"
    optional_reputation_api_key: str | None = None
    official_iasc_url: str = DEFAULT_IASC_URL
    resource_guard_enabled: bool = True
    sync_jobs: bool = False
    min_available_ram_mb: int = 1024
    min_free_disk_mb: int = 2048
    log_dir: Path = Path("/var/log/tanggap60")
    demo_ttl_seconds: int = 3600

    @field_validator("case_storage_dir")
    @classmethod
    def storage_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("CASE_STORAGE_DIR must be an absolute path")
        return value

    @field_validator("official_iasc_url")
    @classmethod
    def iasc_must_be_allowlisted(cls, value: str) -> str:
        normalized = value.rstrip("/") + "/"
        if normalized.rstrip("/") not in {u.rstrip("/") for u in HANDOFF_ALLOWLIST} and value.rstrip(
            "/"
        ) not in {u.rstrip("/") for u in HANDOFF_ALLOWLIST}:
            raise ValueError("OFFICIAL_IASC_URL is not in the handoff allowlist")
        return value

    @property
    def sqlite_path(self) -> Path:
        return sqlite_file_from_url(self.database_url)


def sqlite_file_from_url(url: str) -> Path:
    if not url.startswith("sqlite:"):
        raise ValueError("DATABASE_URL must be sqlite")
    rest = url.split("sqlite:", 1)[1]
    if rest.startswith("////"):
        return Path("/" + rest[4:])
    if rest.startswith("///"):
        return Path(rest[3:])
    raise ValueError("unsupported sqlite URL")
