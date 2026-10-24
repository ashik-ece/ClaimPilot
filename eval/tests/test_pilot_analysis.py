"""Tests use SYNTHETIC rows built here. They check the arithmetic and the exclusion rules and say
nothing about real adjusters."""

import csv
from pathlib import Path

from pilot.analyze import analyze, bootstrap_reduction, read_rows, split_timing_rows

FIELDS = [
    "release", "round", "tester", "position", "source_claim", "mode", "review_seconds",
    "longest_idle_seconds", "outcome", "reason_code", "overridden", "note", "ai_recommendation",
    "gold_decision", "started_at", "decided_at",
]  # fmt: skip


def row(tester: str, position: int, mode: str, seconds: float, idle: int = 10, **kw: object) -> dict:
    base = {
        "release": "v1.0", "round": 1, "tester": tester, "position": position,
        "source_claim": f"CLM-{position:04d}", "mode": mode, "review_seconds": seconds,
        "longest_idle_seconds": idle, "outcome": "APPROVED", "reason_code": "", "overridden": 0,
        "note": "", "ai_recommendation": "approve", "gold_decision": "approve",
        "started_at": "", "decided_at": "",
    }  # fmt: skip
    return {**base, **kw}


def load(tmp_path: Path, rows: list[dict]) -> list[dict]:
    path = tmp_path / "s.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return read_rows([path])


def test_learning_claim_and_interrupted_sessions_are_excluded(tmp_path: Path) -> None:
    rows = load(
        tmp_path,
        [
            row("T1", 1, "assisted", 500),  # learning claim
            row("T1", 2, "manual", 400, idle=301),  # interrupted
            row("T1", 3, "manual", 300),
            row("T1", 4, "assisted", 100),
            row("T1", 5, "assisted", 100, idle=""),  # idle unknown: cannot be trusted
        ],
    )
    kept, dropped = split_timing_rows(rows)
    assert [r["position"] for r in kept] == [3, 4]
    assert dropped == {"learning_claim": 1, "interrupted_or_idle_unknown": 2}


def test_relative_reduction_uses_medians_and_reports_n(tmp_path: Path) -> None:
    rows = load(
        tmp_path,
        [row("T1", 2 + i, "assisted", s) for i, s in enumerate([60, 70, 80])]
        + [row("T1", 10 + i, "manual", s) for i, s in enumerate([100, 120, 140])],
    )
    timing = analyze(rows)["timing"]
    assert timing["n"] == {"assisted": 3, "manual": 3}
    assert timing["median_s"] == {"assisted": 70.0, "manual": 120.0}
    assert timing["relative_reduction"] == round(1 - 70 / 120, 4)
    low, high = timing["relative_reduction_ci95"]
    assert low < timing["relative_reduction"] < high


def test_per_tester_points_are_kept_beside_the_pooled_median(tmp_path: Path) -> None:
    rows = load(
        tmp_path,
        [row("T1", 2, "assisted", 50), row("T1", 3, "manual", 100),
         row("T2", 2, "assisted", 90), row("T2", 3, "manual", 95)],
    )  # fmt: skip
    per = analyze(rows)["timing"]["per_tester"]
    assert per["T1"]["assisted"]["median_s"] == 50 and per["T2"]["assisted"]["median_s"] == 90


def test_override_rate_counts_assisted_decisions_only(tmp_path: Path) -> None:
    rows = load(
        tmp_path,
        [
            row("T1", 2, "assisted", 60, overridden=1, reason_code="wrong_clause", outcome="DENIED"),
            row("T1", 3, "assisted", 60),
            row("T1", 4, "manual", 90, outcome="DENIED"),  # differs from AI but is no override
        ],
    )
    d = analyze(rows)["decisions"]
    assert (d["assisted_decisions"], d["overrides"], d["override_rate"]) == (2, 1, 0.5)
    assert d["override_reasons"] == {"wrong_clause": 1}


def test_a_human_approving_a_deny_label_is_listed(tmp_path: Path) -> None:
    rows = load(tmp_path, [row("T2", 3, "manual", 60, gold_decision="deny", outcome="APPROVED")])
    assert analyze(rows)["decisions"]["labelled_non_approve_but_approved"] == ["T2:CLM-0003:manual"]


def test_too_few_samples_gives_no_interval() -> None:
    assert bootstrap_reduction([10.0], [20.0, 30.0]) is None


def test_one_mode_only_does_not_invent_a_reduction(tmp_path: Path) -> None:
    timing = analyze(load(tmp_path, [row("T1", 2, "assisted", 60)]))["timing"]
    assert "relative_reduction" not in timing
