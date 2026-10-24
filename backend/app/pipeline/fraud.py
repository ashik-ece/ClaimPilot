"""Agent 3: fraud and anomaly flags as pure, unit-tested rules.

A flag is a reason for a human to look harder, never a finding of fraud, and the rules never
approve or deny anything. Each rule returns None when it cannot tell (a missing term or fact)
and reports that in `unchecked`, so the recommender escalates instead of assuming the claim is
clean.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.pipeline.terms import PolicyTerms

_REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "typical_repair_cost.json").read_text()
)
TYPICAL_COST = Decimal(str(_REFERENCE["median_usd"]))
INFLATED_MULTIPLE = Decimal(str(_REFERENCE["inflated_multiple"]))

INFLATED = "inflated_estimate"
OUTSIDE_PERIOD = "date_outside_policy_period"
DUPLICATE = "duplicate_claim"


@dataclass(frozen=True)
class Flag:
    type: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FraudResult:
    flags: list[Flag]
    unchecked: list[str]  # rules that could not run for lack of a fact or policy term

    @property
    def types(self) -> list[str]:
        return [f.type for f in self.flags]


def _amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def check_period(outside_period: bool | None) -> Flag | None:
    if outside_period:
        return Flag(OUTSIDE_PERIOD, "the loss date falls outside the policy period")
    return None


def check_inflated(
    total: Decimal | None,
    high_value_threshold: Decimal | None,
    typical: Decimal = TYPICAL_COST,
    multiple: Decimal = INFLATED_MULTIPLE,
) -> Flag | None:
    """Claimed amount well above the typical repair cost.

    Amounts at or above the policy's high-value threshold are excluded: clause 6.1 already
    sends those to a senior adjuster, and a large loss is not in itself a sign of inflation.
    """
    if total is None or high_value_threshold is None:
        return None
    if multiple * typical < total < high_value_threshold:
        return Flag(
            INFLATED,
            f"claimed {total} is more than {multiple}x the typical repair cost of {typical}",
            {"total": str(total), "typical": str(typical), "multiple": str(multiple)},
        )
    return None


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _vehicle(facts: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(_norm(facts.get(k)) for k in ("vehicle_make", "vehicle_model", "vehicle_year"))  # type: ignore[return-value]


def check_duplicate(
    facts: Mapping[str, Any], earlier: Iterable[tuple[str, Mapping[str, Any]]]
) -> Flag | None:
    """An earlier claim on the same policy, incident date and vehicle reports the same loss."""
    policy, date_, vehicle = (
        _norm(facts.get("policy_number")),
        _norm(facts.get("incident_date")),
        _vehicle(facts),
    )
    if not (policy and date_ and all(vehicle)):
        return None
    for other_id, other in earlier:
        if (
            _norm(other.get("policy_number")) == policy
            and _norm(other.get("incident_date")) == date_
            and _vehicle(other) == vehicle
        ):
            return Flag(DUPLICATE, f"matches earlier claim {other_id}", {"duplicate_of": other_id})
    return None


def evaluate(
    facts: Mapping[str, Any],
    terms: PolicyTerms,
    checks: Mapping[str, Any],
    earlier: Iterable[tuple[str, Mapping[str, Any]]] = (),
) -> FraudResult:
    flags: list[Flag] = []
    unchecked: list[str] = []
    total = _amount(facts.get("total_amount"))

    if checks.get("outside_period") is None:
        unchecked.append(OUTSIDE_PERIOD)
    elif flag := check_period(checks["outside_period"]):
        flags.append(flag)

    if total is None or terms.high_value_threshold is None:
        unchecked.append(INFLATED)
    elif flag := check_inflated(total, terms.high_value_threshold):
        flags.append(flag)

    if not (facts.get("policy_number") and facts.get("incident_date") and all(_vehicle(facts))):
        unchecked.append(DUPLICATE)
    elif flag := check_duplicate(facts, earlier):
        flags.append(flag)
    return FraudResult(flags, unchecked)
