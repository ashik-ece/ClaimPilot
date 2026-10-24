from harness import metrics
from harness.run import load_labels, score


def test_field_normalisation() -> None:
    assert metrics.field_correct("total_amount", "7050.00", "$7,050")
    assert not metrics.field_correct("total_amount", "7050.00", "$7,060.00")
    assert metrics.field_correct("claimant_name", "Megan Zimmerman", "megan  zimmerman")
    assert metrics.field_correct("policy_number", "POL-BASIC-897132", "POL. BASIC 897132")
    assert not metrics.field_correct("claimant_name", "A", None)


def test_absent_field_must_stay_null() -> None:
    assert metrics.field_correct("police_report_number", None, None)
    assert not metrics.field_correct("police_report_number", None, "PR-IL-2026-1")
    assert not metrics.field_correct("police_report_number", "PR-IL-2026-1", None)


def test_itemized_damages_order_independent() -> None:
    a = [{"description": "Door", "amount": "10.00"}, {"description": "Paint", "amount": "5.5"}]
    b = [{"description": "paint", "amount": "5.50"}, {"description": "Door", "amount": "10"}]
    assert metrics.field_correct("itemized_damages", a, b)
    assert not metrics.field_correct("itemized_damages", a, b[:1])


def test_decision_confusion_and_missing() -> None:
    rep = metrics.decision_report(
        {"1": "approve", "2": "deny", "3": "escalate"}, {"1": "approve", "2": "approve", "3": None}
    )
    assert rep["accuracy"] == 1 / 3
    assert rep["confusion"]["deny"]["approve"] == 1
    assert rep["confusion"]["escalate"]["none"] == 1


def test_fraud_report_and_approved_fraud_rule() -> None:
    labels = {"1": ["duplicate_claim"], "2": [], "3": ["inflated_estimate"]}
    flags = {"1": ["duplicate_claim"], "2": ["inflated_estimate"], "3": []}
    rep = metrics.fraud_report(labels, flags, {"1": "escalate", "3": "approve"})
    assert rep["per_flag"]["duplicate_claim"]["recall"] == 1.0
    assert rep["per_flag"]["inflated_estimate"]["precision"] == 0.0
    assert rep["fraud_claims_approved"] == ["3"]


def test_citation_report() -> None:
    rep = metrics.citation_report({"1": ["A-1"], "2": ["B-2"]}, {"1": ["A-1", "X-9"], "2": []})
    assert rep["recall"] == 0.5 and rep["precision"] == 0.5


def test_perfect_oracle_scores_100_on_dev() -> None:
    labels = load_labels("dev")
    preds = {
        c: {
            "fields": v["fields"],
            "decision": v["decision"],
            "fraud_flags": [f if isinstance(f, str) else f["type"] for f in v["fraud_flags"]],
            "cited_clause_ids": v["governing_clause_ids"],
        }
        for c, v in labels.items()
    }
    rep = score(labels, preds)
    assert rep["extraction"]["macro"] == 1.0
    assert rep["decision"]["accuracy"] == 1.0
    assert rep["citations"]["recall"] == 1.0 and rep["fraud"]["fraud_claims_approved"] == []
