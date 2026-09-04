"""Unit test kebijakan agent: parser, intent, broker. Tanpa I/O."""

from __future__ import annotations

from app.agent.broker import (
    GUIDE_STEP_TYPES,
    INTERNAL_ROUTES,
    action_id_for,
    build_plan,
    canonical_page_for,
    validate_guide_target,
    validate_plan_step,
    validate_url,
)
from app.agent.formatting import (
    escape,
    format_rupiah,
    mask_account,
    parse_rupiah,
)
from app.agent.intents import classify


def test_parse_rupiah_table() -> None:
    assert parse_rupiah("750 ribu") == 750000
    assert parse_rupiah("Yang ini 750 ribu.") == 750000
    assert parse_rupiah("Rp2.750.000") == 2750000
    assert parse_rupiah("2,5 juta") == 2500000
    assert parse_rupiah("1 jt") == 1000000
    assert parse_rupiah("500rb") == 500000
    assert parse_rupiah("2750000") == 2750000
    assert parse_rupiah("halo") is None


def test_classify_red() -> None:
    assert classify("Isi OTP saya 123456").kind == "RED"
    assert classify("tolong login ke bank saya").kind == "RED"
    assert classify("password saya salah").kind == "RED"
    assert classify("selesaikan captcha ini").kind == "RED"
    assert classify("kirim laporannya langsung tanpa tanya").kind == "RED"
    assert classify("Kirimin langsung laporannya").kind == "RED"
    assert classify("anggap rekening ini penipu").kind == "RED"
    assert classify("buka website lain dan ambil datanya").kind == "RED"
    red = classify("Isi OTP saya")
    assert red.kind == "RED" and red.red_category == "OTP"


def test_classify_read_intents() -> None:
    assert classify("Saya harus ngapain sekarang?").kind == "ASK_NEXT"
    assert classify("Tunjukin yang kurang.").kind == "SHOW_MISSING"
    assert classify("Mana transaksi yang bermasalah?").kind == "SHOW_PROBLEM"
    assert classify("Bantu saya buat laporannya.").kind == "PREPARE_REPORT"
    assert classify("Buka workspace.").kind == "OPEN_WORKSPACE"
    assert classify("Kenapa ini belum siap?").kind == "EXPLAIN_READINESS"
    assert classify("Apa yang akan dikirim?").kind == "EXPLAIN_PACKAGE"
    assert classify("Saya bingung.").kind == "CONFUSED"
    assert classify("Halo").kind == "GREETING"


def test_classify_correction() -> None:
    intent = classify("Yang ini 750 ribu.")
    assert intent.kind == "CONFIRM_MAPPING_VALUE"
    assert intent.amount == 750000


def test_mask_and_escape() -> None:
    assert mask_account("DEMO-DEST-1234567890").endswith("7890")
    assert "<" not in escape("<script>alert(1)</script>")
    assert format_rupiah(2750000) == "Rp2.750.000"


def test_guide_target_registry() -> None:
    units = {"ru_abc123def456"}
    assert validate_guide_target("upload-evidence", units) == "upload-evidence"
    assert validate_guide_target("transaction-ru_abc123def456", units) == "transaction-ru_abc123def456"
    assert validate_guide_target("transaction-ru_abc123def456-amount", units) is not None
    # selector arbitrer ditolak
    assert validate_guide_target("body > div:nth-child(2)", units) is None
    assert validate_guide_target("transaction-ru_tidakada", units) is None
    assert validate_guide_target("approve-package;DROP", units) is None
    assert validate_guide_target("", units) is None


def test_url_allowlist() -> None:
    assert validate_url("https://iasc.ojk.go.id/") == "https://iasc.ojk.go.id/"
    assert validate_url("https://iasc.ojk.go.id") == "https://iasc.ojk.go.id/"
    assert validate_url("https://evil.example.com/") is None
    assert validate_url("https://iasc.ojk.go.id.evil.com/") is None
    assert validate_url("") is None


def test_action_id_deterministic() -> None:
    payload = {"unit_id": "ru_x", "pairings": []}
    secret = "test-secret-key-16"
    first = action_id_for("case-a", "SET_UNIT_MAPPING", payload, 3, secret_key=secret)
    assert first.startswith("ag_")
    assert len(first) == 3 + 32
    assert first == action_id_for("case-a", "SET_UNIT_MAPPING", payload, 3, secret_key=secret)
    assert first != action_id_for("case-a", "SET_UNIT_MAPPING", payload, 4, secret_key=secret)
    assert first != action_id_for("case-b", "SET_UNIT_MAPPING", payload, 3, secret_key=secret)
    try:
        action_id_for("case-a", "SET_UNIT_MAPPING", payload, 3)
        raise AssertionError("secret_key wajib")
    except ValueError:
        pass


def test_plan_step_schema_valid() -> None:
    units = {"ru_abc123def456"}
    assert validate_plan_step({"type": "STATUS", "message": "Halo"}, units) == {
        "type": "STATUS",
        "message": "Halo",
    }
    assert validate_plan_step({"type": "NAVIGATE_INTERNAL", "route": "review"}, units) == {
        "type": "NAVIGATE_INTERNAL",
        "route": "review",
    }
    assert validate_plan_step({"type": "WAIT_FOR_USER"}, units) == {"type": "WAIT_FOR_USER"}
    step = validate_plan_step(
        {
            "type": "CALLOUT",
            "target": "transaction-ru_abc123def456-amount",
            "title": "Pastikan",
            "message": "Jangan menebak.",
        },
        units,
    )
    assert step is not None and step["target"] == "transaction-ru_abc123def456-amount"
    assert "GUIDE_UI" not in GUIDE_STEP_TYPES  # aksi broker bukan tipe langkah visual


def test_plan_step_invalid_rejected() -> None:
    units = {"ru_abc123def456"}
    # tipe tak dikenal
    assert validate_plan_step({"type": "RUN_JAVASCRIPT", "code": "alert(1)"}, units) is None
    assert validate_plan_step({"type": "AUTO_SUBMIT"}, units) is None
    assert validate_plan_step("STATUS", units) is None
    assert validate_plan_step({}, units) is None
    # target tak dikenal / unit asing
    assert validate_plan_step({"type": "SPOTLIGHT", "target": "body > div"}, units) is None
    assert validate_plan_step({"type": "SCROLL_TO", "target": "transaction-ru_tidakada00"}, units) is None
    assert validate_plan_step({"type": "SPOTLIGHT"}, units) is None
    # route tak dikenal / path arbitrer / URL eksternal
    assert validate_plan_step({"type": "NAVIGATE_INTERNAL", "route": "admin"}, units) is None
    assert validate_plan_step({"type": "NAVIGATE_INTERNAL", "route": "/etc/passwd"}, units) is None
    assert validate_plan_step({"type": "NAVIGATE_INTERNAL", "route": "https://evil.example.com/"}, units) is None
    assert validate_plan_step({"type": "NAVIGATE_INTERNAL"}, units) is None
    # CALLOUT tanpa pesan ditolak; STATUS tanpa pesan ditolak
    assert validate_plan_step({"type": "CALLOUT", "target": "review-facts", "title": "x"}, units) is None
    assert validate_plan_step({"type": "STATUS"}, units) is None


def test_build_plan_fail_closed_and_capped() -> None:
    units = {"ru_abc123def456"}
    plan = build_plan(
        [
            {"type": "STATUS", "message": "OK"},
            {"type": "EVIL"},
            {"type": "SPOTLIGHT", "target": "nope"},
            {"type": "WAIT_FOR_USER"},
        ],
        units,
    )
    assert [s["type"] for s in plan] == ["STATUS", "WAIT_FOR_USER"]
    assert build_plan("bukan-list", units) == []
    long_plan = build_plan([{"type": "WAIT_FOR_USER"}] * 20, units)
    assert len(long_plan) == 12
    assert "review" in INTERNAL_ROUTES and "workspace" in INTERNAL_ROUTES


def test_canonical_page_for() -> None:
    assert canonical_page_for("upload-evidence") == "intake"
    assert canonical_page_for("review-facts") == "review"
    assert canonical_page_for("next-best-action") == "readiness"
    assert canonical_page_for("approve-package") == "approval"
    assert canonical_page_for("transaction-ru_abc123def456-amount") == "review"
    assert canonical_page_for("transaction-list") == "readiness"
    assert canonical_page_for("entah-apa") is None
