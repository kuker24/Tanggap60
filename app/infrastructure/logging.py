from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

REDACT_KEYS = {
    "raw_value",
    "normalized_value",
    "ticket",
    "account",
    "phone",
    "prompt",
    "password",
    "otp",
    "secret",
    "extracted_text",
    "evidence_text",
}
SAFE_EVENT_FIELDS = {
    "timestamp",
    "level",
    "service",
    "request_id",
    "case_id_hash",
    "run_id",
    "state_before",
    "state_after",
    "tool_name",
    "tool_version",
    "duration_ms",
    "result_code",
    "error_code",
    "retry_count",
    "rss_mb",
    "artifact_type",
    "sha256_prefix",
    "message",
    "event_type",
}


class RedactingFilter(logging.Filter):
    _sensitive = re.compile(
        r"(Rp\s?[\d.]+|\b\d{10,}\b|otp|password|ktp|rekening)",
        re.IGNORECASE,
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sensitive.sub("[redacted]", record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "tanggap60"),
            "message": record.getMessage(),
        }
        for key in SAFE_EVENT_FIELDS:
            if hasattr(record, key) and key not in payload:
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service: str = "tanggap60-web") -> logging.Logger:
    logger = logging.getLogger("tanggap60")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger = logging.LoggerAdapter(logger, {"service": service})  # type: ignore[assignment]
    return logger  # type: ignore[return-value]


def hash_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()[:12]
