from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

OFFICIAL_DOMAINS = frozenset({"iasc.ojk.go.id", "ojk.go.id", "lapor.go.id"})


@dataclass
class UrlIndicator:
    name: str
    finding: str
    source: str
    checked_at: str


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def analyze_url(raw: str) -> tuple[list[UrlIndicator], bool]:
    indicators: list[UrlIndicator] = []
    fetched = False
    parsed = urlparse(raw.strip())
    host = (parsed.hostname or "").lower()
    checked = _now()
    if not host:
        indicators.append(UrlIndicator("struktur_url", "URL tidak lengkap", "urlparse", checked))
        return indicators, fetched
    if parsed.username or parsed.password:
        indicators.append(
            UrlIndicator("kredensial_di_url", "Ada informasi mirip kredensial pada URL", "urlparse", checked)
        )
    try:
        ascii_host = host.encode("idna").decode("ascii")
        if ascii_host != host or "xn--" in host:
            indicators.append(UrlIndicator("punycode", "Domain memakai karakter/punycode", "idna", checked))
        host = ascii_host
    except UnicodeError:
        indicators.append(UrlIndicator("punycode", "Domain tidak dapat dinormalisasi", "idna", checked))
    if _is_blocked_ip(host):
        indicators.append(
            UrlIndicator(
                "alamat_lokal",
                "Host mengarah ke alamat lokal/metadata; server tidak akan membukanya",
                "ip_literal",
                checked,
            )
        )
    try:
        port = parsed.port
    except ValueError:
        indicators.append(UrlIndicator("port_tidak_valid", "Port URL tidak valid", "urlparse", checked))
        return indicators, fetched
    if port and port not in {80, 443}:
        indicators.append(UrlIndicator("port_tidak_lazim", f"Port {port} tidak lazim", "urlparse", checked))
    labels = [part for part in host.split(".") if part]
    if len(labels) >= 5:
        indicators.append(
            UrlIndicator("subdomain_berlebih", "Subdomain bertingkat banyak", "urlparse", checked)
        )
    if any(part in {"login", "secure", "ojk", "bank", "iasc"} for part in labels[:-2]):
        if host not in OFFICIAL_DOMAINS and not host.endswith(".ojk.go.id"):
            indicators.append(
                UrlIndicator(
                    "menyerupai_resmi",
                    "Subdomain menyerupai lembaga resmi tetapi bukan domain allowlist",
                    "allowlist_fixture",
                    checked,
                )
            )
    if host not in OFFICIAL_DOMAINS and any(d in host for d in ("ojk", "iasc", "bank")):
        if not host.endswith(".go.id"):
            indicators.append(
                UrlIndicator(
                    "bukan_domain_resmi",
                    "Bukan domain resmi pada daftar fixture",
                    "allowlist_fixture",
                    checked,
                )
            )
    return indicators, fetched


def _is_blocked_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "metadata.google.internal"}
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or host.startswith("169.254.")
        or str(ip) == "169.254.169.254"
    )


def reputation_unavailable() -> UrlIndicator:
    return UrlIndicator(
        name="reputasi_eksternal",
        finding="Sinyal reputasi eksternal sedang tidak tersedia. Kami tidak akan menebak.",
        source="OPTIONAL_SIGNAL_UNAVAILABLE",
        checked_at=_now(),
    )
