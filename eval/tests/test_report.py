from harness.report import classify_errors, dangerous, interval


def label(decision: str, governing: list[str], flags: list[str] | None = None) -> dict:
    return {
        "decision": decision,
        "governing_clause_ids": governing,
        "fraud_flags": flags or [],
        "fields": {"policy_number": "POL-STD-111111", "total_amount": "100.00"},
    }


def pred(decision: str, cited: list[str], policy: str = "POL-STD-111111") -> dict:
    return {
        "decision": decision,
        "cited_clause_ids": cited,
        "fields": {"policy_number": policy, "total_amount": "100.00"},
    }


def test_errors_are_attributed_in_pipeline_order() -> None:
    labels = {
        "A": label("deny", ["STD-5.1"]),
        "B": label("deny", ["STD-5.1"]),
        "C": label("deny", ["STD-5.1"]),
        "D": label("approve", ["STD-2.1"]),
    }
    preds = {
        "A": pred("approve", ["STD-5.1"], policy="POL-STD-999999"),  # misread field
        "B": pred("approve", ["STD-2.1"]),  # governing clause never cited
        "C": pred("escalate", ["STD-5.1"]),  # right clause, wrong call
        "D": pred("approve", ["STD-2.1"]),  # correct: not an error
    }
    assert classify_errors(labels, preds) == {
        "extraction": ["A"],
        "retrieval": ["B"],
        "reasoning": ["C"],
    }


def test_dangerous_quadrant_is_a_claim_that_should_not_be_approved_but_was() -> None:
    labels = {
        "deny": label("deny", []),
        "esc": label("escalate", []),
        "fraud": label("approve", [], ["duplicate_claim"]),
        "ok": label("approve", []),
        "cautious": label("deny", []),
    }
    preds = {
        "deny": pred("approve", []),
        "esc": pred("approve", []),
        "fraud": pred("approve", []),
        "ok": pred("approve", []),
        "cautious": pred("escalate", []),
    }
    assert dangerous(labels, preds) == ["deny", "esc", "fraud"]


def test_interval_brackets_the_mean_and_needs_two_claims() -> None:
    values = [1.0] * 90 + [0.0] * 10
    low, high = interval(values)  # type: ignore[misc]
    assert low < 0.9 < high
    assert interval([1.0]) is None
