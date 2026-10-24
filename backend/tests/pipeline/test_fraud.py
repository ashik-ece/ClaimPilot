from datetime import date
from decimal import Decimal

from app.pipeline import fraud
from app.pipeline.terms import PolicyTerms

TERMS = PolicyTerms(date(2026, 1, 1), date(2026, 12, 31), 30, Decimal(30000), {})
FACTS = {
    "policy_number": "POL-STD-111111",
    "incident_date": "2026-03-01",
    "vehicle_make": "Honda",
    "vehicle_model": "Civic",
    "vehicle_year": 2018,
    "total_amount": "7000.00",
}
CLEAN_CHECKS = {"outside_period": False, "late_notice": False, "high_value": False}


def test_inflated_window_is_between_multiple_of_typical_and_high_value() -> None:
    cap = Decimal(30000)
    assert fraud.check_inflated(Decimal(7000), cap) is None
    assert fraud.check_inflated(Decimal("13440.00"), cap) is None  # exactly 2x is not above it
    assert fraud.check_inflated(Decimal("13440.01"), cap) is not None
    assert fraud.check_inflated(Decimal(29999), cap) is not None
    assert fraud.check_inflated(Decimal(30000), cap) is None  # high-value claims are not 'inflated'
    assert (
        fraud.check_inflated(None, cap) is None
        and fraud.check_inflated(Decimal(20000), None) is None
    )


def test_duplicate_needs_same_policy_date_and_vehicle() -> None:
    same = dict(FACTS, total_amount="8000.00", claimant_name="Someone")
    assert fraud.check_duplicate(FACTS, [("CLM-1", same)]).evidence == {"duplicate_of": "CLM-1"}  # type: ignore[union-attr]
    for change in (
        {"incident_date": "2026-03-02"},
        {"vehicle_model": "Accord"},
        {"policy_number": "POL-STD-222222"},
    ):
        assert fraud.check_duplicate(FACTS, [("CLM-1", dict(FACTS, **change))]) is None


def test_duplicate_ignores_formatting_differences() -> None:
    other = dict(FACTS, policy_number="pol std 111111", vehicle_make="HONDA ", vehicle_year="2018")
    assert fraud.check_duplicate(FACTS, [("CLM-9", other)]) is not None


def test_evaluate_collects_flags_and_reports_what_it_could_not_check() -> None:
    res = fraud.evaluate(dict(FACTS, total_amount="20000.00"), TERMS, {"outside_period": True})
    assert sorted(res.types) == [fraud.OUTSIDE_PERIOD, fraud.INFLATED] and res.unchecked == []
    blind = fraud.evaluate({"total_amount": None}, TERMS, {"outside_period": None})
    assert set(blind.unchecked) == {fraud.OUTSIDE_PERIOD, fraud.INFLATED, fraud.DUPLICATE}
    assert fraud.evaluate(FACTS, TERMS, CLEAN_CHECKS).flags == []
