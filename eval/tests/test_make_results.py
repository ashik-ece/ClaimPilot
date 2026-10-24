from make_results import pilot_section

REAL = {
    "simulated": False, "warning": None, "testers": ["T1"], "decisions_total": 12,
    "timing": {"n": {"assisted": 5, "manual": 5}, "median_s": {"assisted": 100, "manual": 200},
               "relative_reduction": 0.5, "relative_reduction_ci95": [0.3, 0.6], "dropped": {},
               "per_tester": {"T1": {"assisted": {"median_s": 100, "n": 5}, "manual": {"median_s": 200, "n": 5}}}},
    "decisions": {"override_rate": 0.1, "overrides": 1, "assisted_decisions": 10, "override_reasons": {}},
}  # fmt: skip
SIM = {**REAL, "simulated": True, "warning": "SIMULATED DATA. x", "testers": ["SIM-T1"]}


def text(lines: list[str]) -> str:
    return "\n".join(lines)


def test_without_a_real_pilot_the_simulation_is_shown_as_not_evidence() -> None:
    out = text(pilot_section(None, SIM))
    assert "Real pilot: not run yet" in out and "NOT EVIDENCE" in out and "SIMULATED DATA" in out


def test_a_real_pilot_replaces_the_simulation_entirely() -> None:
    out = text(pilot_section(REAL, SIM))
    assert "SIMULATED" not in out and "not run yet" not in out and "Pilot results" in out


def test_with_neither_the_section_says_nothing_exists() -> None:
    out = text(pilot_section(None, None))
    assert "not run yet" in out and "Relative time reduction" not in out
