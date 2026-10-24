import itertools

import pytest

from app.states import ALLOWED, TERMINAL, ClaimStatus, IllegalTransition, check_transition

S = ClaimStatus


def test_happy_path_is_allowed() -> None:
    path = [S.RECEIVED, S.EXTRACTED, S.ANALYZED, S.AWAITING_ADJUDICATION, S.APPROVED]
    for a, b in itertools.pairwise(path):
        check_transition(a, b, "agent:pipeline" if b not in TERMINAL else "user:1")


def test_cannot_skip_stages() -> None:
    with pytest.raises(IllegalTransition):
        check_transition(S.RECEIVED, S.APPROVED, "user:1")


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_are_final(terminal: ClaimStatus) -> None:
    assert ALLOWED[terminal] == frozenset()


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_agents_cannot_reach_terminal_states(terminal: ClaimStatus) -> None:
    with pytest.raises(IllegalTransition):
        check_transition(S.AWAITING_ADJUDICATION, terminal, "agent:recommender")


def test_human_can_resolve_needs_review() -> None:
    check_transition(S.NEEDS_REVIEW, S.DENIED, "user:1")
