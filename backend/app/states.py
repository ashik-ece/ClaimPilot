"""Claim lifecycle. All allowed transitions live here and nowhere else."""

from enum import StrEnum


class ClaimStatus(StrEnum):
    RECEIVED = "RECEIVED"
    EXTRACTED = "EXTRACTED"
    ANALYZED = "ANALYZED"
    AWAITING_ADJUDICATION = "AWAITING_ADJUDICATION"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    ESCALATED = "ESCALATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


TERMINAL = frozenset({ClaimStatus.APPROVED, ClaimStatus.DENIED, ClaimStatus.ESCALATED})

_S = ClaimStatus
ALLOWED: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    _S.RECEIVED: frozenset({_S.EXTRACTED, _S.NEEDS_REVIEW}),
    _S.EXTRACTED: frozenset({_S.ANALYZED, _S.NEEDS_REVIEW}),
    _S.ANALYZED: frozenset({_S.AWAITING_ADJUDICATION, _S.NEEDS_REVIEW}),
    _S.AWAITING_ADJUDICATION: frozenset(TERMINAL),
    # A human can always proceed without AI output.
    _S.NEEDS_REVIEW: frozenset({_S.AWAITING_ADJUDICATION} | TERMINAL),
    _S.APPROVED: frozenset(),
    _S.DENIED: frozenset(),
    _S.ESCALATED: frozenset(),
}


class IllegalTransition(Exception):
    pass


def is_agent(actor: str) -> bool:
    return actor.startswith("agent:")


def check_transition(current: ClaimStatus, target: ClaimStatus, actor: str) -> None:
    if target not in ALLOWED[current]:
        raise IllegalTransition(f"{current} -> {target} is not allowed")
    if target in TERMINAL and is_agent(actor):
        raise IllegalTransition(f"actor {actor} cannot move a claim to {target}")
