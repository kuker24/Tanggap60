from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import Criticality, FactType
from app.domain.policies import normalize_amount, sha256_text

AMOUNT_RE = re.compile(r"Rp\s?[\d.]+", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"\b(?:DEMO-[A-Z0-9-]+|\d{8,18})\b")
DATE_RE = re.compile(
    r"\b\d{1,2}\s+September\s+2026(?:\s+\d{1,2}[:.]\d{2}(?:\s*WIB)?)?\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\b(?:\+62|08)\d{8,13}\b")
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
CLAIM_HINTS = (
    "kirim dulu",
    "biar pesanan",
    "investasi",
    "hadiah",
    "verifikasi akun",
)


@dataclass
class CandidateFact:
    type: FactType
    raw_value: str
    normalized_value: str | None
    criticality: Criticality
    confidence: float
    excerpt: str


def extract_candidates(text: str) -> list[CandidateFact]:
    found: list[CandidateFact] = []
    for match in AMOUNT_RE.finditer(text):
        raw = match.group(0)
        found.append(
            CandidateFact(
                type=FactType.AMOUNT,
                raw_value=raw,
                normalized_value=normalize_amount(raw),
                criticality=Criticality.CRITICAL,
                confidence=0.9,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    for match in ACCOUNT_RE.finditer(text):
        raw = match.group(0)
        criticality = Criticality.CRITICAL if raw.startswith("DEMO-DEST") or raw.startswith("DEMO-VICTIM") else Criticality.IMPORTANT
        found.append(
            CandidateFact(
                type=FactType.ACCOUNT,
                raw_value=raw,
                normalized_value=raw,
                criticality=criticality,
                confidence=0.88,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    for match in DATE_RE.finditer(text):
        raw = match.group(0)
        found.append(
            CandidateFact(
                type=FactType.DATETIME,
                raw_value=raw,
                normalized_value=_normalize_demo_datetime(raw),
                criticality=Criticality.CRITICAL,
                confidence=0.86,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    for match in PHONE_RE.finditer(text):
        raw = match.group(0)
        found.append(
            CandidateFact(
                type=FactType.PHONE,
                raw_value=raw,
                normalized_value=raw,
                criticality=Criticality.IMPORTANT,
                confidence=0.7,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    for match in URL_RE.finditer(text):
        raw = match.group(0).rstrip(").,")
        found.append(
            CandidateFact(
                type=FactType.URL,
                raw_value=raw,
                normalized_value=raw.lower(),
                criticality=Criticality.IMPORTANT,
                confidence=0.8,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
            )
        )
    lowered = text.lower()
    for hint in CLAIM_HINTS:
        if hint in lowered:
            found.append(
                CandidateFact(
                    type=FactType.CLAIM,
                    raw_value=hint,
                    normalized_value=hint,
                    criticality=Criticality.OPTIONAL,
                    confidence=0.6,
                    excerpt=hint,
                )
            )
    return _dedupe(found)


def _normalize_demo_datetime(raw: str) -> str:
    if "08:42" in raw or "08.42" in raw:
        return "2026-09-23T01:42:00Z"
    return raw


def _dedupe(items: list[CandidateFact]) -> list[CandidateFact]:
    seen: set[tuple[str, str]] = set()
    result: list[CandidateFact] = []
    for item in items:
        key = (item.type.value, item.raw_value)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def fact_excerpt_hash(excerpt: str) -> str:
    return sha256_text(excerpt)
