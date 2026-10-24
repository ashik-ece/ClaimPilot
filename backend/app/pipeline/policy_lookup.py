"""Resolve the policy (and version) that governs a claim from the policy number on its form."""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Policy

_CLAIM_POLICY = re.compile(r"^(?P<base>POL-[A-Z]+)-\d{6}$")


def resolve_policy(
    session: Session, claimed_number: str | None, incident: date | None
) -> Policy | None:
    """The version in force on the incident date; if none is, the latest version, so the
    period check can report the loss as outside it. None if the policy is unknown."""
    m = _CLAIM_POLICY.match(claimed_number or "")
    if not m:
        return None
    versions = list(
        session.scalars(
            select(Policy).where(Policy.policy_number == m["base"]).order_by(Policy.effective_date)
        )
    )
    if not versions:
        return None
    if incident:
        for p in versions:
            if p.effective_date <= incident <= p.expiry_date:
                return p
    return versions[-1]
