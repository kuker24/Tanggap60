from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC
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
                Route.PRE_INCIDENT_CHECK,
                "Ada nominal di bukti; pastikan uang sudah terkirim sebelum pindah alur",
                0.7,
                True,
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


_MONTH_NUM = {
    "january": 1,
    "januari": 1,
    "february": 2,
    "februari": 2,
    "march": 3,
    "maret": 3,
    "april": 4,
    "may": 5,
    "mei": 5,
    "june": 6,
    "juni": 6,
    "july": 7,
    "juli": 7,
    "august": 8,
    "agustus": 8,
    "september": 9,
    "october": 10,
    "oktober": 10,
    "november": 11,
    "december": 12,
    "desember": 12,
}
_MONTH_ID = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}
_TZ_HOURS = {"WIB": 7, "WITA": 8, "WIT": 9}


def normalize_amount(raw: str) -> str | None:
    text = re.sub(r"^[Rr][Pp]\.?\s*", "", raw.strip()).replace(" ", "").rstrip(".,")
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    if "," in text and "." in text:
        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_comma > last_dot:
            intpart, frac = text[:last_comma].replace(".", ""), text[last_comma + 1 :]
        else:
            intpart, frac = text[:last_dot].replace(",", ""), text[last_dot + 1 :]
        if not intpart.isdigit() or not frac.isdigit():
            return None
        return str(int(intpart))
    if "," in text:
        parts = text.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2 and parts[0].replace(".", "").isdigit():
            return str(int(parts[0].replace(".", "") or "0"))
        if all(p.isdigit() for p in parts) and parts[0] and all(len(p) == 3 for p in parts[1:]):
            return str(int("".join(parts)))
        return None
    if "." in text:
        parts = text.split(".")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2 and parts[0].isdigit():
            return str(int(parts[0]))
        if all(p.isdigit() for p in parts) and parts[0] and all(len(p) == 3 for p in parts[1:]):
            return str(int("".join(parts)))
        return None
    return None


def normalize_datetime(raw: str) -> str | None:
    from datetime import datetime, timedelta, timezone

    text = " ".join(raw.strip().split())
    named = re.match(
        r"^(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})(?:\s+(\d{1,2})[:.](\d{2})(?:\s*(WIB|WITA|WIT))?)?$",
        text,
        re.IGNORECASE,
    )
    if named:
        day, month, year, hour, minute, zone = named.groups()
        month_n = _MONTH_NUM.get(month.lower())
        if month_n is None:
            return None
        try:
            if hour is None:
                datetime(int(year), month_n, int(day))
                return f"{int(year):04d}-{month_n:02d}-{int(day):02d}"
            offset = _TZ_HOURS.get((zone or "").upper())
            local = datetime(int(year), month_n, int(day), int(hour), int(minute))
        except ValueError:
            return None
        if offset is None:
            return local.strftime("%Y-%m-%dT%H:%M:%S")
        aware = local.replace(tzinfo=timezone(timedelta(hours=offset)))
        return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    numeric = re.match(
        r"^(\d{1,2})/(\d{1,2})/(20\d{2})(?:\s+(\d{1,2})[:.](\d{2}))?$",
        text,
    )
    if numeric:
        day, month, year, hour, minute = numeric.groups()
        try:
            if hour is None:
                datetime(int(year), int(month), int(day))
                return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            local = datetime(int(year), int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None
        return local.strftime("%Y-%m-%dT%H:%M:%S")
    iso = re.match(r"^(20\d{2})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(Z)?)?$", text)
    if iso:
        return text
    return None


def format_when(normalized: str | None) -> tuple[str, str]:
    from datetime import datetime, timedelta, timezone

    value = (normalized or "").strip()
    if not value:
        return "", ""
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        year, month, day = (int(p) for p in value.split("-"))
        return f"{day} {_MONTH_ID[month]} {year}", ""
    stamp = value
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return value, ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=7)))
    date = f"{parsed.day} {_MONTH_ID[parsed.month]} {parsed.year}"
    if parsed.hour == 0 and parsed.minute == 0 and "T" not in value and " " not in value:
        return date, ""
    has_time = "T" in value or re.search(r"\d{1,2}:\d{2}", value)
    if not has_time:
        return date, ""
    zone = " WIB" if parsed.tzinfo is not None or value.endswith("Z") else ""
    return date, f"{parsed:%H:%M}{zone}"


def normalize_ticket(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()
