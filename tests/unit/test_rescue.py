from datetime import UTC, datetime, timedelta

from app.domain.models import (
    Criticality,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    FactRecord,
    FactType,
    ReviewStatus,
)
from app.services.rescue import build_adversarial_checks, build_bank_call_script, build_golden_window

NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def _fact(kind: FactType, value: str, *, status: ReviewStatus = ReviewStatus.CONFIRMED) -> FactRecord:
    return FactRecord(
        fact_id=f"fact-{kind.value}",
        case_id="case-x",
        type=kind,
        raw_value=value,
        normalized_value=value,
        criticality=Criticality.CRITICAL,
        confidence=0.9,
        review_status=status,
        source_evidence_id="ev-1",
        source_page=1,
        source_bbox="p1:1-2",
        source_excerpt_hash="a" * 64,
    )


def _evidence() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev-1",
        case_id="case-x",
        kind=EvidenceKind.IMAGE,
        original_name_display="transfer.png",
        storage_key="ev-1",
        mime="image/png",
        size_bytes=10,
        sha256="a" * 64,
        page_count=1,
        status=EvidenceStatus.EXTRACTED,
        retention_until=NOW,
    )


def test_golden_window_prioritizes_device_compromise_and_never_executes() -> None:
    incident = (NOW - timedelta(minutes=25)).isoformat()
    result = build_golden_window(
        facts=[_fact(FactType.DATETIME, incident), _fact(FactType.CLAIM, "diminta pasang APK remote")],
        evidence=[_evidence()],
        conflicts=[],
        next_action={"label": "Hubungi bank"},
        now=NOW,
    )
    assert result["band"] == "Bertindak sekarang"
    assert "koneksi perangkat" in result["action"].lower()
    assert result["guidance_only"] is True


def test_golden_window_ignores_rejected_compromise_claim() -> None:
    result = build_golden_window(
        facts=[_fact(FactType.CLAIM, "APK remote", status=ReviewStatus.REJECTED), _fact(FactType.AMOUNT, "2750000")],
        evidence=[_evidence()],
        conflicts=[],
        next_action={"label": "Hubungi bank resmi", "reason": "Transaksi sudah ditinjau."},
        now=NOW,
    )
    assert result["scope"] == "Dana sudah berpindah"
    assert result["action"] == "Hubungi bank resmi"


def test_adversarial_checker_ranks_blocking_and_limits_questions() -> None:
    def check(check_id: str, blocking: bool) -> dict:
        return {
            "check_id": check_id,
            "status": "MISSING",
            "blocking": blocking,
            "action": f"Lengkapi {check_id}",
            "reason": "belum tersedia",
        }

    report = {
        "units": [
            {
                "unit_id": "ru-1",
                "channels": [
                    {"label": "Bank/PJP", "checks": [check("OPTIONAL", False), check("AMOUNT", True), check("TIME", True), check("DEST", True)]}
                ],
            }
        ]
    }
    result = build_adversarial_checks(report)
    assert len(result["findings"]) == 3
    assert all(item["blocking"] for item in result["findings"])
    assert result["remaining_count"] == 1
    assert "bukan keputusan" in result["disclaimer"]


def test_adversarial_checker_passes_empty_report() -> None:
    result = build_adversarial_checks(None)
    assert result["passed"] is True
    assert result["findings"] == []


def test_adversarial_challenge_stays_softenable_and_reads_mid_sentence() -> None:
    from app.web.labels import soften

    report = {
        "units": [
            {
                "unit_id": "ru_50bb2d583129",
                "channels": [
                    {
                        "label": "Bank/PJP",
                        "checks": [
                            {
                                "check_id": "PAIRING",
                                "status": "CONFLICT",
                                "blocking": True,
                                "action": "Pilih pasangan transaksi yang benar",
                                "reason": "AMBIGUOUS_MAPPING pada ru_50bb2d583129",
                            },
                            {
                                "check_id": "CHANNEL",
                                "status": "MISSING",
                                "blocking": False,
                                "action": "Tambahkan kanal atau nama PJP bila diketahui",
                                "reason": "Belum ada fakta ditinjau",
                            },
                        ],
                    }
                ],
            }
        ]
    }
    findings = build_adversarial_checks(report)["findings"]
    rendered = [soften(item["challenge"]) for item in findings]

    # the all-caps token must survive the service so soften() can translate it
    assert rendered[0] == "Bank mungkin meminta klarifikasi karena transaksi yang belum terpasang."
    # a normal sentence-cased reason reads lowercase mid-sentence
    assert rendered[1] == "Bank mungkin meminta klarifikasi karena belum ada fakta ditinjau."
    for text in rendered:
        assert "ru_" not in text
        assert "AMBIGUOUS_MAPPING" not in text
        assert "PJP" not in text
        assert " pada ." not in text


def test_bank_call_script_generation_for_bca() -> None:
    facts = [
        _fact(FactType.PJP, "Bank Central Asia (BCA)"),
        _fact(FactType.ACCOUNT, "1234567890"),
        _fact(FactType.PERSON_NAME, "Budi Penipu"),
        _fact(FactType.AMOUNT, "15000000"),
        _fact(FactType.DATETIME, "2026-09-18T10:30:00Z"),
    ]
    script = build_bank_call_script(facts)
    assert script["has_data"] is True
    assert "BCA" in script["bank_name"]
    assert script["hotline"] == "1500888"
    assert script["hotline_tel"] == "1500888"
    assert "1234567890" in script["destination_account"]
    assert "Budi Penipu" in script["destination_name"]
    assert "15.000.000" in script["amount"]
    assert "temporary freeze" in script["script_text"]
    assert "1234567890" in script["script_text"]


def test_bank_call_script_empty_facts() -> None:
    script = build_bank_call_script([])
    assert script["has_data"] is False
    assert script["destination_account"] == "Perlu dikonfirmasi"


def test_golden_window_includes_bank_call() -> None:
    facts = [
        _fact(FactType.PJP, "Mandiri"),
        _fact(FactType.ACCOUNT, "987654321"),
        _fact(FactType.AMOUNT, "5000000"),
    ]
    result = build_golden_window(
        facts=facts,
        evidence=[_evidence()],
        conflicts=[],
        next_action={"label": "Hubungi bank"},
        now=NOW,
    )
    assert "bank_call" in result
    assert result["bank_call"]["has_data"] is True
    assert "14000" in result["bank_call"]["hotline"]

