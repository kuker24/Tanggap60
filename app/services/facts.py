from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from app.domain.models import Criticality, FactType
from app.domain.policies import normalize_amount, normalize_datetime, sha256_text
from app.services.extraction import locator_for

AMOUNT_RE = re.compile(r"Rp\.?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"\b(?:DEMO-(?:DEST|VICTIM)-[A-Z0-9-]+|\d{8,18})\b")
MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)"
)
DATE_RE = re.compile(
    rf"\b(?:\d{{1,2}}\s+{MONTHS}\s+20\d{{2}}(?:\s+\d{{1,2}}[:.]\d{{2}}(?:\s*(?:WIB|WITA|WIT))?)?|"
    rf"\d{{1,2}}/\d{{1,2}}/20\d{{2}}(?:\s+\d{{1,2}}[:.]\d{{2}})?)\b",
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
    page: int = 1
    locator: str = ""


def extract_candidates(
    text: str,
    *,
    page: int = 1,
    boxes: list[tuple[str, int, int, int, int]] | None = None,
) -> list[CandidateFact]:
    found: list[CandidateFact] = []
    box_list = boxes or []
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
                page=page,
                locator=locator_for(raw, page, match.start(), match.end(), box_list),
            )
        )
    phone_spans = [(m.start(), m.end(), m.group(0)) for m in PHONE_RE.finditer(text)]
    phone_values = {span[2] for span in phone_spans}
    for match in ACCOUNT_RE.finditer(text):
        raw = match.group(0)
        if raw in phone_values or PHONE_RE.fullmatch(raw):
            continue
        criticality = (
            Criticality.CRITICAL if raw.startswith("DEMO-DEST") or raw.startswith("DEMO-VICTIM") else Criticality.IMPORTANT
        )
        found.append(
            CandidateFact(
                type=FactType.ACCOUNT,
                raw_value=raw,
                normalized_value=raw,
                criticality=criticality,
                confidence=0.88,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
                page=page,
                locator=locator_for(raw, page, match.start(), match.end(), box_list),
            )
        )
    for match in DATE_RE.finditer(text):
        raw = match.group(0)
        found.append(
            CandidateFact(
                type=FactType.DATETIME,
                raw_value=raw,
                normalized_value=normalize_datetime(raw) or raw,
                criticality=Criticality.CRITICAL,
                confidence=0.86,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
                page=page,
                locator=locator_for(raw, page, match.start(), match.end(), box_list),
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
                page=page,
                locator=locator_for(raw, page, match.start(), match.end(), box_list),
            )
        )
    for match in URL_RE.finditer(text):
        raw = match.group(0).rstrip(").,")
        found.append(
            CandidateFact(
                type=FactType.URL,
                raw_value=raw,
                normalized_value=_normalize_url(raw),
                criticality=Criticality.IMPORTANT,
                confidence=0.8,
                excerpt=text[max(0, match.start() - 20) : match.end() + 20],
                page=page,
                locator=locator_for(raw, page, match.start(), match.end(), box_list),
            )
        )
    lowered = text.lower()
    for hint in CLAIM_HINTS:
        idx = lowered.find(hint)
        if idx >= 0:
            found.append(
                CandidateFact(
                    type=FactType.CLAIM,
                    raw_value=hint,
                    normalized_value=hint,
                    criticality=Criticality.OPTIONAL,
                    confidence=0.6,
                    excerpt=hint,
                    page=page,
                    locator=locator_for(hint, page, idx, idx + len(hint), box_list),
                )
            )
    return _dedupe(found)


def _normalize_url(raw: str) -> str:
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if not host:
        return raw
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        return raw
    if port:
        netloc = f"{host}:{port}"
    if parsed.username:
        user = parsed.username
        if parsed.password:
            user = f"{user}:{parsed.password}"
        netloc = f"{user}@{netloc}"
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


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
