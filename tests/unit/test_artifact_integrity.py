from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.services.artifacts import transaction_time_or_none

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json").read_text())


def test_transaction_time_not_fabricated() -> None:
    assert transaction_time_or_none("") is None
    assert transaction_time_or_none(None) is None
    assert transaction_time_or_none("23 September 2026") is None
    assert transaction_time_or_none("2026-09-23T01:42:00Z") == "2026-09-23T01:42:00Z"


def test_schema_2_0_legacy_without_readiness() -> None:
    payload = {
        "schema_version": "2.0",
        "case_id": "case-old-20",
        "mode": "DEMO",
        "route": "POST_INCIDENT_RESPONSE",
        "state": "HANDOFF_READY",
        "facts": [],
        "conflicts": [],
        "actions": [],
        "artifacts": [],
        "official_status": "NOT_VERIFIED",
    }
    jsonschema.validate(payload, SCHEMA)


def test_schema_2_1_postincident_requires_readiness() -> None:
    payload = {
        "schema_version": "2.1",
        "case_id": "case-new-21",
        "mode": "DEMO",
        "route": "POST_INCIDENT_RESPONSE",
        "state": "HANDOFF_READY",
        "facts": [],
        "conflicts": [],
        "actions": [],
        "artifacts": [],
        "official_status": "NOT_VERIFIED",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)
    payload["readiness"] = {
        "case_id": "case-new-21",
        "profile_version": "2026-09-02.mvp2",
        "overall_status": "NEEDS_ACTION",
        "channels": [],
        "official_status": "NOT_VERIFIED",
        "disclaimer": "internal",
    }
    jsonschema.validate(payload, SCHEMA)


def test_schema_allows_null_transferred_at() -> None:
    payload = {
        "schema_version": "2.1",
        "case_id": "case-time-null",
        "mode": "DEMO",
        "route": "POST_INCIDENT_RESPONSE",
        "state": "HANDOFF_READY",
        "facts": [],
        "conflicts": [],
        "transactions": [
            {
                "transaction_id": "tx-1",
                "victim_account": None,
                "destination_account": "DEMO-DEST-01",
                "amount": 1,
                "currency": "IDR",
                "transferred_at": None,
            }
        ],
        "actions": [],
        "artifacts": [],
        "official_status": "NOT_VERIFIED",
        "readiness": {
            "case_id": "case-time-null",
            "profile_version": "2026-09-02.mvp2",
            "overall_status": "NEEDS_ACTION",
            "channels": [],
            "official_status": "NOT_VERIFIED",
            "disclaimer": "internal",
        },
    }
    jsonschema.validate(payload, SCHEMA)
