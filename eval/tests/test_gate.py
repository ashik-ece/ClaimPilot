import json
from pathlib import Path

from harness.gate import evaluate

THRESHOLDS = json.loads((Path(__file__).resolve().parent.parent / "thresholds.json").read_text())
RESULTS = Path(__file__).resolve().parent.parent / "results"


def failures(report: dict) -> list[str]:  # type: ignore[type-arg]
    return [name for name, ok, _ in evaluate(report, THRESHOLDS) if not ok]


def test_current_dev_run_passes_the_gate() -> None:
    assert failures(json.loads((RESULTS / "pipeline_dev.json").read_text())) == []


def test_a_regression_in_decisions_fails_the_gate() -> None:
    report = json.loads((RESULTS / "pipeline_dev.json").read_text())
    report["decision"]["accuracy"] = 0.80  # e.g. a bad prompt change
    assert failures(report) == ["decision.accuracy >= 0.9"]


def test_one_fraud_claim_approved_fails_the_gate() -> None:
    report = json.loads((RESULTS / "pipeline_dev.json").read_text())
    report["fraud"]["fraud_claims_approved"] = ["CLM-0001"]
    assert failures(report) == ["fraud.fraud_claims_approved len<= 0"]


def test_a_missing_metric_fails_rather_than_passes() -> None:
    report = json.loads((RESULTS / "pipeline_dev.json").read_text())
    del report["citations"]
    assert failures(report) == ["citations.recall >= 0.9"]


def test_a_partial_run_fails_the_gate() -> None:
    report = json.loads((RESULTS / "pipeline_dev.json").read_text())
    report["predicted"] = 12
    assert failures(report) == ["predicted >= 40"]
