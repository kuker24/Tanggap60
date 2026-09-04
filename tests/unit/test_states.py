from __future__ import annotations

import pytest

from app.domain.errors import InvalidStateTransition
from app.domain.states import FORBIDDEN_EXAMPLES, State, can_transition, transition


def test_allowed_happy_path() -> None:
    path = [
        State.NEW,
        State.INGESTING,
        State.EXTRACTING,
        State.REVIEW_REQUIRED,
        State.READY_FOR_ACTION,
        State.WAITING_APPROVAL,
        State.GENERATING,
        State.VERIFYING,
        State.HANDOFF_READY,
        State.RECEIPT_RECORDED,
        State.COMPLETE,
    ]
    for current, nxt in zip(path, path[1:]):
        assert can_transition(current, nxt)
        assert transition(current, nxt) == nxt


@pytest.mark.parametrize("current,target", FORBIDDEN_EXAMPLES)
def test_forbidden_transitions(current: State, target: State) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidStateTransition):
        transition(current, target)


def test_purge_from_any() -> None:
    for state in State:
        if state == State.PURGED:
            continue
        assert can_transition(state, State.PURGED)


def test_preapproval_can_return_to_ingesting() -> None:
    for current in (
        State.EXTRACTING,
        State.REVIEW_REQUIRED,
        State.READY_FOR_ACTION,
        State.WAITING_APPROVAL,
    ):
        assert can_transition(current, State.INGESTING)
        assert transition(current, State.INGESTING) == State.INGESTING
