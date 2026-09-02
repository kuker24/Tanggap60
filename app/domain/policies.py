from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.domain.errors import OpenConflicts
from app.domain.models import (
    REVIEWED_FACT_STATUSES,
    ConflictRecord,
    ConflictSeverity,
    ConflictStatus,
    FactRecord,
    ReviewStatus,
)
from app.domain.states import DeclaredCondition, Route, State

CRITICAL_POST_TYPES = {"AMOUNT", "ACCOUNT", "DATETIME"}
ABSOLUTE_COPY = (
    "pasti aman",
    "pasti scam",
    "dijamin aman",
    "sudah dipastikan penipuan",
    "laporan resmi berhasil",
)


def blocking_conflicts_open(conflicts: list[ConflictRecord]) -> list[ConflictRecord]:
    return [
        c
        for c in conflicts
        if c.severity == ConflictSeverity.BLOCKING and c.status == ConflictStatus.OPEN
    ]


def assert_no_blocking_conflicts(conflicts: list[ConflictRecord]) -> None:
    open_blocking = blocking_conflicts_open(conflicts)
    if open_blocking:
        raise OpenConflicts("Selesaikan konflik wajib sebelum lanjut")


def critical_facts_reviewed(facts: list[FactRecord]) -> bool:
    critical = [
        f
        for f in facts
        if f.criticality.value == "CRITICAL" and f.review_status != ReviewStatus.REJECTED
    ]
    if not critical:
        return True
    return all(f.review_status in REVIEWED_FACT_STATUSES for f in critical)


def reviewed_facts(facts: list[FactRecord]) -> list[FactRecord]:
    return [
        f
        for f in facts
        if f.review_status
        in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED, ReviewStatus.UNAVAILABLE}
    ]


def route_from_condition(
    condition: DeclaredCondition, *, has_loss_facts: bool
) -> tuple[Route, str, float, bool]:
    if condition == DeclaredCondition.AFTER_LOSS:
        return Route.POST_INCIDENT_RESPONSE, "Pengguna menyatakan sudah rugi", 0.9, False
    if condition == DeclaredCondition.BEFORE_LOSS:
        if has_loss_facts:
            return (
                Route.POST_INCIDENT_RESPONSE,
                "Ada indikasi dana terkirim; alur pascainsiden",
                0.7,
                False,
            )
        return Route.PRE_INCIDENT_CHECK, "Pengguna menyatakan belum rugi", 0.85, False
    return Route.OUT_OF_SCOPE, "Kondisi belum jelas", 0.4, True


def can_enter_pre(condition: DeclaredCondition, route: Route) -> bool:
    return condition == DeclaredCondition.BEFORE_LOSS and route == Route.PRE_INCIDENT_CHECK


def approval_allowed(state: State, conflicts: list[ConflictRecord], facts: list[FactRecord]) -> bool:
    if state != State.WAITING_APPROVAL:
        return False
    if blocking_conflicts_open(conflicts):
        return False
    return critical_facts_reviewed(facts)


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_payload(
    *,
    facts: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    route: str,
    actions: list[dict[str, Any]],
    notice_version: str,
    template_version: str,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "actions": actions,
        "conflicts": conflicts,
        "facts": facts,
        "notice_version": notice_version,
        "route": route,
        "template_version": template_version,
    }
    if readiness is not None:
        payload["readiness"] = readiness
        payload["readiness_profile_version"] = readiness.get("profile_version")
    return payload


def mask_ticket(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value)
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return cleaned[:2] + ("*" * max(len(cleaned) - 4, 1)) + cleaned[-2:]


def ticket_plausible(value: str) -> bool:
    cleaned = re.sub(r"[\s-]", "", value)
    return bool(re.fullmatch(r"[A-Za-z0-9]{6,32}", cleaned))


def contains_absolute_copy(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ABSOLUTE_COPY)


def normalize_amount(raw: str) -> str | None:
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    return str(int(digits))


def normalize_ticket(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()
