from __future__ import annotations

import logging

from app.infrastructure.logging import RedactingFilter


def test_redact_amount() -> None:
    record = logging.LogRecord("x", logging.INFO, "", 1, "transfer Rp2.750.000 secret", None, None)
    RedactingFilter().filter(record)
    assert "2.750" not in str(record.msg)
