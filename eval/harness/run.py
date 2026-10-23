"""Score a predictions file against the ground-truth labels for one split.

    uv run python -m harness.run --predictions results/preds_dev.json --split dev --name extraction_v1

Predictions are {claim_id: {"fields": {...}, "decision": "approve|deny|escalate",
"fraud_flags": ["inflated_estimate"], "cited_clause_ids": ["STD-4.2"]}}. Any key may be absent
(for example, when scoring extraction alone). The report lands in results/<name>.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness import metrics

DATA = Path(__file__).resolve().parent.parent / "data"
RESULTS = Path(__file__).resolve().parent.parent / "results"


def load_labels(split: str, data: Path = DATA) -> dict[str, dict[str, Any]]:
    ids = json.loads((data / "splits.json").read_text())
    wanted = ids["dev"] + ids["test"] if split == "all" else ids[split]
    return {i: json.loads((data / "claims" / f"{i}.json").read_text()) for i in wanted}


def score(labels: dict[str, dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {"claims": len(labels), "predicted": len(set(labels) & set(preds))}
    if any("fields" in p for p in preds.values()):
        report["extraction"] = metrics.field_accuracy(
            {c: v["fields"] for c, v in labels.items()},
            {c: p.get("fields", {}) for c, p in preds.items()},
        )
    decisions = {c: p.get("decision") for c, p in preds.items()}
    if any(decisions.values()):
        report["decision"] = metrics.decision_report({c: v["decision"] for c, v in labels.items()}, decisions)
    flags = {c: p.get("fraud_flags", []) for c, p in preds.items() if "fraud_flags" in p}
    if flags:
        report["fraud"] = metrics.fraud_report(
            {c: [f if isinstance(f, str) else f["type"] for f in v["fraud_flags"]] for c, v in labels.items()},
            flags,
            decisions,
        )
    cited = {c: p["cited_clause_ids"] for c, p in preds.items() if "cited_clause_ids" in p}
    if cited:
        report["citations"] = metrics.citation_report({c: v["governing_clause_ids"] for c, v in labels.items()}, cited)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    report = score(load_labels(args.split), json.loads(args.predictions.read_text()))
    report["split"] = args.split
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.name}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: v for k, v in report.items() if k != "extraction"}, indent=2)[:2000])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
