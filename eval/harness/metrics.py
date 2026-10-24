"""Pure scoring functions for the agent pipeline. No I/O, so they are easy to unit test."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

DECISIONS = ("approve", "deny", "escalate")
SCALAR_FIELDS = (
    "claimant_name",
    "policy_number",
    "incident_date",
    "reported_date",
    "incident_type",
    "incident_location",
    "vehicle_make",
    "vehicle_model",
    "vehicle_year",
    "police_report_number",
    "total_amount",
)
ALL_FIELDS = (*SCALAR_FIELDS, "itemized_damages")


def _norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[^0-9.\-]", "", str(value))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def field_correct(name: str, expected: Any, predicted: Any) -> bool:
    """Exact match after normalisation: money by value, everything else ignoring case/punctuation."""
    if expected is None or predicted is None:
        # A null label means the document genuinely lacks the field; inventing a value is wrong.
        return expected is None and predicted in (None, "")
    if name == "total_amount":
        a, b = _money(expected), _money(predicted)
        return a is not None and a == b
    if name == "itemized_damages":
        want = Counter((_norm_text(i["description"]), _money(i["amount"])) for i in expected)
        try:
            got = Counter((_norm_text(i["description"]), _money(i["amount"])) for i in predicted)
        except (KeyError, TypeError):
            return False
        return want == got
    return _norm_text(expected) == _norm_text(predicted)


def field_accuracy(
    labels: Mapping[str, Mapping[str, Any]], predictions: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Per-field accuracy over claims, and the macro average (the success metric)."""
    hits: dict[str, int] = defaultdict(int)
    for claim_id, truth in labels.items():
        pred = predictions.get(claim_id, {})
        for name in ALL_FIELDS:
            if name in truth and field_correct(name, truth[name], pred.get(name)):
                hits[name] += 1
    n = len(labels)
    per_field = {name: hits[name] / n for name in ALL_FIELDS} if n else {}
    return {
        "per_field": per_field,
        "macro": sum(per_field.values()) / len(per_field) if per_field else 0.0,
        "min_field": min(per_field.values()) if per_field else 0.0,
        "claims": n,
    }


def confusion_matrix(pairs: Iterable[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """matrix[true][predicted]; an unparseable/missing prediction counts as 'none'."""
    matrix: dict[str, dict[str, int]] = {t: dict.fromkeys((*DECISIONS, "none"), 0) for t in DECISIONS}
    for truth, pred in pairs:
        matrix[truth][pred if pred in DECISIONS else "none"] += 1
    return matrix


def decision_report(truths: Mapping[str, str], predictions: Mapping[str, str | None]) -> dict[str, Any]:
    matrix = confusion_matrix((t, predictions.get(c) or "none") for c, t in truths.items())
    total = sum(sum(row.values()) for row in matrix.values())
    correct = sum(matrix[d][d] for d in DECISIONS)
    return {
        "accuracy": correct / total if total else 0.0,
        "confusion": matrix,
        "claims": total,
    }


def fraud_report(
    labels: Mapping[str, list[str]],
    flags: Mapping[str, list[str]],
    decisions: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Per-flag precision/recall, plus the hard rule: no fraud claim may be recommended 'approve'."""
    kinds = sorted({k for ks in labels.values() for k in ks} | {k for ks in flags.values() for k in ks})
    per_flag: dict[str, dict[str, float | int]] = {}
    for kind in kinds:
        tp = sum(1 for c, ks in labels.items() if kind in ks and kind in flags.get(c, []))
        fp = sum(1 for c, ks in flags.items() if kind in ks and kind not in labels.get(c, []))
        fn = sum(1 for c, ks in labels.items() if kind in ks and kind not in flags.get(c, []))
        per_flag[kind] = {
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    fraud_claims = [c for c, ks in labels.items() if ks]
    approved_fraud = [c for c in fraud_claims if decisions and decisions.get(c) == "approve"]
    return {"per_flag": per_flag, "fraud_claims_approved": sorted(approved_fraud)}


def citation_report(labels: Mapping[str, list[str]], cited: Mapping[str, list[str]]) -> dict[str, Any]:
    """Share of claims whose governing clause is cited, and share of cited clauses that are governing."""
    recalled = recalled_all = precise = n_cited = 0
    for claim_id, gold in labels.items():
        got = set(cited.get(claim_id, []))
        recalled += bool(set(gold) & got)
        recalled_all += set(gold) <= got
        precise += len(got & set(gold))
        n_cited += len(got)
    n = len(labels)
    return {
        "recall": recalled / n if n else 0.0,
        "recall_all": recalled_all / n if n else 0.0,
        "precision": precise / n_cited if n_cited else 0.0,
        "claims": n,
    }
