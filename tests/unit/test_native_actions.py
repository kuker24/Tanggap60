"""Native Action Mode: registry allowlist, validasi fail-closed, kontrak hasil."""

from __future__ import annotations

from app.agent.broker import build_plan, validate_plan_step
from app.agent.native_actions import (
    GREEN_NATIVE,
    RED_NATIVE,
    REGISTRY,
    YELLOW_NATIVE,
    action_result,
    validate_native_action,
)

UID_A = "ru_abcdef123456"
UID_X = "ru_999999999999"
AMOUNT_F = "fact-amount000001"
DEST_F = "fact-dest0000001"

CONTEXT = {
    "unit_ids": [UID_A],
    "units": [
        {
            "unit_id": UID_A,
            "mapping_status": "AMBIGUOUS",
            "candidates": [
                {"fact_id": AMOUNT_F, "type": "AMOUNT", "value": "750000"},
                {"fact_id": DEST_F, "type": "ACCOUNT", "value": "DEMO-DEST-B"},
            ],
        }
    ],
}


def test_registry_splits_green_yellow() -> None:
    assert {"OPEN_TRANSACTION", "FOCUS_TX_FIELD", "OPEN_EVIDENCE", "OPEN_WORKSPACE_VIEW"} <= GREEN_NATIVE
    assert YELLOW_NATIVE == {"SET_DRAFT"}
    assert "SET_DRAFT" not in GREEN_NATIVE


def test_valid_open_transaction() -> None:
    clean = validate_native_action({"action": "OPEN_TRANSACTION", "risk": "GREEN", "target": UID_A}, CONTEXT)
    assert clean == {"action": "OPEN_TRANSACTION", "risk": "GREEN", "target": UID_A}


def test_valid_set_draft() -> None:
    clean = validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "amount",
         "fact_id": AMOUNT_F, "label": "Rp750.000"},
        CONTEXT,
    )
    assert clean is not None and clean["fact_id"] == AMOUNT_F


def test_unknown_action_rejected() -> None:
    assert validate_native_action({"action": "CLICK_ANYTHING", "risk": "GREEN", "target": UID_A}, CONTEXT) is None
    assert validate_native_action({"action": "document.querySelector", "risk": "GREEN"}, CONTEXT) is None
    assert validate_native_action({"action": "OPEN_TRANSACTION", "risk": "GREEN"}, CONTEXT) is None  # tanpa target


def test_red_actions_rejected() -> None:
    for name in RED_NATIVE:
        assert validate_native_action({"action": name, "risk": "RED", "target": UID_A}, CONTEXT) is None, name
    assert len(RED_NATIVE) >= 9


def test_cross_case_unit_rejected() -> None:
    assert validate_native_action({"action": "OPEN_TRANSACTION", "risk": "GREEN", "target": UID_X}, CONTEXT) is None
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_X, "field": "amount",
         "fact_id": AMOUNT_F, "label": "Rp750.000"},
        CONTEXT,
    ) is None


def test_risk_spoof_rejected() -> None:
    assert validate_native_action({"action": "OPEN_TRANSACTION", "risk": "YELLOW", "target": UID_A}, CONTEXT) is None
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "GREEN", "target": UID_A, "field": "amount",
         "fact_id": AMOUNT_F, "label": "Rp750.000"},
        CONTEXT,
    ) is None


def test_wrong_candidate_type_rejected() -> None:
    # fact AMOUNT tidak sah untuk field destination; fact asing tidak sah.
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "destination",
         "fact_id": AMOUNT_F, "label": "x"},
        CONTEXT,
    ) is None
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "amount",
         "fact_id": "fact-asing000001", "label": "x"},
        CONTEXT,
    ) is None
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "password",
         "fact_id": AMOUNT_F, "label": "x"},
        CONTEXT,
    ) is None


def test_hostile_payloads_rejected() -> None:
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "amount",
         "fact_id": 'x"][value="y', "label": "x"},
        CONTEXT,
    ) is None
    assert validate_native_action(
        {"action": "SET_DRAFT", "risk": "YELLOW", "target": UID_A, "field": "amount",
         "fact_id": AMOUNT_F, "label": "x <script>alert(1)</script>"},
        CONTEXT,
    ) is None
    assert validate_native_action({"action": "OPEN_TRANSACTION", "risk": "GREEN",
                                   "target": "transaction-ru_abcdef123456",
                                   "label": "https://evil.example/"}, CONTEXT) is None


def test_non_dict_rejected() -> None:
    assert validate_native_action("OPEN_TRANSACTION", CONTEXT) is None
    assert validate_native_action(None, CONTEXT) is None
    assert validate_native_action({"action": "SET_DRAFT"}, CONTEXT) is None


def test_action_result_contract() -> None:
    ok = action_result("OPEN_TRANSACTION", "COMPLETED", message="Transaksi dibuka.")
    assert ok == {"action": "OPEN_TRANSACTION", "status": "COMPLETED", "changed": False,
                  "requires_human": False, "message": "Transaksi dibuka."}
    wait = action_result("SET_DRAFT", "WAITING_APPROVAL", changed=True, requires_human=True, message="Draf.")
    assert wait["status"] == "WAITING_APPROVAL" and wait["requires_human"] is True
    denied = action_result("FILL_OTP", "DENIED", reason="SENSITIVE_ACTION")
    assert denied["status"] == "DENIED" and denied["reason"] == "SENSITIVE_ACTION"
    weird = action_result("X", "SOMETHING_ELSE")
    assert weird["status"] == "DENIED"


def test_plan_step_act_types_validated() -> None:
    units = {UID_A}
    assert validate_plan_step({"type": "OPEN_TRANSACTION", "target": f"transaction-{UID_A}"}, units) == {
        "type": "OPEN_TRANSACTION", "target": f"transaction-{UID_A}"}
    assert validate_plan_step({"type": "OPEN_TRANSACTION", "target": f"transaction-{UID_X}"}, units) is None
    assert validate_plan_step({"type": "FOCUS_FIELD", "target": f"transaction-{UID_A}"}, units) is None  # tanpa field
    good_focus = validate_plan_step({"type": "FOCUS_FIELD", "target": f"transaction-{UID_A}-amount"}, units)
    assert good_focus is not None
    good_draft = validate_plan_step(
        {"type": "SET_DRAFT", "unit": UID_A, "field": "amount", "fact_id": AMOUNT_F, "label": "Rp750.000"}, units)
    assert good_draft is not None
    assert validate_plan_step(
        {"type": "SET_DRAFT", "unit": UID_A, "field": "hacker", "fact_id": AMOUNT_F, "label": "x"}, units) is None
    assert validate_plan_step(
        {"type": "SET_DRAFT", "unit": UID_X, "field": "amount", "fact_id": AMOUNT_F, "label": "x"}, units) is None


def test_plan_cap_12_with_act_steps() -> None:
    units = {UID_A}
    steps = [
        {"type": "STATUS", "message": "x"},
        {"type": "NAVIGATE_INTERNAL", "route": "review"},
        {"type": "OPEN_TRANSACTION", "target": f"transaction-{UID_A}"},
        {"type": "SPOTLIGHT", "target": f"transaction-{UID_A}"},
        {"type": "MOVE_POINTER", "target": f"transaction-{UID_A}-amount"},
        {"type": "SET_DRAFT", "unit": UID_A, "field": "amount", "fact_id": AMOUNT_F, "label": "Rp750.000"},
        {"type": "CALLOUT", "target": f"transaction-{UID_A}-amount", "title": "t", "message": "m"},
        {"type": "WAIT_FOR_USER"},
    ]
    plan = build_plan(steps * 2, units)
    assert len(plan) == 12
    assert REGISTRY  # registry terimpor dan tak kosong
