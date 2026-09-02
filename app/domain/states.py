from __future__ import annotations

from enum import StrEnum

from app.domain.errors import InvalidStateTransition


class State(StrEnum):
    NEW = "NEW"
    INGESTING = "INGESTING"
    EXTRACTING = "EXTRACTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    READY_FOR_ACTION = "READY_FOR_ACTION"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    GENERATING = "GENERATING"
    VERIFYING = "VERIFYING"
    HANDOFF_READY = "HANDOFF_READY"
    RECEIPT_RECORDED = "RECEIPT_RECORDED"
    COMPLETE = "COMPLETE"
    FAILED_SAFE = "FAILED_SAFE"
    PURGED = "PURGED"


class Mode(StrEnum):
    DEMO = "DEMO"
    STANDARD = "STANDARD"


class Route(StrEnum):
    PRE_INCIDENT_CHECK = "PRE_INCIDENT_CHECK"
    POST_INCIDENT_RESPONSE = "POST_INCIDENT_RESPONSE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class DeclaredCondition(StrEnum):
    BEFORE_LOSS = "BEFORE_LOSS"
    AFTER_LOSS = "AFTER_LOSS"
    UNKNOWN = "UNKNOWN"


ALLOWED_TRANSITIONS: frozenset[tuple[State, State]] = frozenset(
    {
        (State.NEW, State.INGESTING),
        (State.NEW, State.EXTRACTING),
        (State.INGESTING, State.EXTRACTING),
        (State.INGESTING, State.FAILED_SAFE),
        (State.EXTRACTING, State.REVIEW_REQUIRED),
        (State.EXTRACTING, State.FAILED_SAFE),
        (State.REVIEW_REQUIRED, State.READY_FOR_ACTION),
        (State.REVIEW_REQUIRED, State.REVIEW_REQUIRED),
        (State.REVIEW_REQUIRED, State.FAILED_SAFE),
        (State.READY_FOR_ACTION, State.WAITING_APPROVAL),
        (State.READY_FOR_ACTION, State.REVIEW_REQUIRED),
        (State.READY_FOR_ACTION, State.FAILED_SAFE),
        (State.WAITING_APPROVAL, State.GENERATING),
        (State.WAITING_APPROVAL, State.REVIEW_REQUIRED),
        (State.WAITING_APPROVAL, State.READY_FOR_ACTION),
        (State.WAITING_APPROVAL, State.FAILED_SAFE),
        (State.GENERATING, State.REVIEW_REQUIRED),
        (State.VERIFYING, State.REVIEW_REQUIRED),
        (State.HANDOFF_READY, State.REVIEW_REQUIRED),
        (State.RECEIPT_RECORDED, State.REVIEW_REQUIRED),
        (State.GENERATING, State.VERIFYING),
        (State.GENERATING, State.FAILED_SAFE),
        (State.VERIFYING, State.HANDOFF_READY),
        (State.VERIFYING, State.FAILED_SAFE),
        (State.HANDOFF_READY, State.RECEIPT_RECORDED),
        (State.HANDOFF_READY, State.COMPLETE),
        (State.RECEIPT_RECORDED, State.COMPLETE),
        (State.RECEIPT_RECORDED, State.RECEIPT_RECORDED),
        (State.FAILED_SAFE, State.INGESTING),
        (State.FAILED_SAFE, State.EXTRACTING),
        (State.FAILED_SAFE, State.GENERATING),
        (State.FAILED_SAFE, State.REVIEW_REQUIRED),
        (State.FAILED_SAFE, State.VERIFYING),
        *{(state, State.PURGED) for state in State if state != State.PURGED},
    }
)

FORBIDDEN_EXAMPLES: tuple[tuple[State, State], ...] = (
    (State.NEW, State.HANDOFF_READY),
    (State.REVIEW_REQUIRED, State.GENERATING),
    (State.WAITING_APPROVAL, State.HANDOFF_READY),
    (State.INGESTING, State.WAITING_APPROVAL),
    (State.PURGED, State.NEW),
)


def can_transition(current: State, target: State) -> bool:
    if current == target and current in {State.REVIEW_REQUIRED, State.RECEIPT_RECORDED}:
        return True
    return (current, target) in ALLOWED_TRANSITIONS


def transition(current: State, target: State) -> State:
    if not can_transition(current, target):
        raise InvalidStateTransition(f"{current} -> {target} is not allowed")
    return target


TOOLS_BY_STATE: dict[State, tuple[str, ...]] = {
    State.INGESTING: ("inspect_evidence",),
    State.EXTRACTING: ("extract_candidate_facts", "validate_case_facts"),
    State.REVIEW_REQUIRED: ("validate_case_facts", "compile_reporting_units"),
    State.READY_FOR_ACTION: (
        "build_preincident_brief",
        "build_postincident_plan",
        "compile_reporting_units",
        "assess_handoff_readiness",
        "recommend_next_action",
    ),
    State.WAITING_APPROVAL: (),
    State.GENERATING: ("compile_artifacts",),
    State.VERIFYING: ("verify_artifacts",),
    State.HANDOFF_READY: ("prepare_official_handoff", "record_handoff_receipt"),
    State.RECEIPT_RECORDED: ("record_handoff_receipt",),
    State.COMPLETE: (),
    State.FAILED_SAFE: (),
}
