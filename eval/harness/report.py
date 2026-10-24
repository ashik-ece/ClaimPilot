"""One command: score a pipeline run on a split and write a results file you can defend.

    uv run python -m harness.report --split test --predictions results/preds_pipeline_test.json \
        --name offline_test [--retrieval results/preds_retrieve_test.json]

The output records the commit, models and prompt versions the run used, the frozen-set check, 95%
bootstrap intervals, an error taxonomy that separates extraction errors, retrieval misses and
reasoning errors, and the dangerous quadrant (a claim that should not be approved, approved).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from pathlib import Path
from typing import Any

from dataset.freeze import DATA as FROZEN_DATA
from dataset.freeze import check as testset_unchanged
from harness import metrics
from harness.run import RESULTS, load_labels, score

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP_SAMPLES = 10_000
SEED = 20261024
WANT = {"approve": "approve", "deny": "deny", "escalate": "escalate"}


def interval(per_claim: list[float], seed: int = SEED) -> list[float] | None:
    """95% bootstrap interval of the mean over claims."""
    n = len(per_claim)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(per_claim, k=n)) / n for _ in range(BOOTSTRAP_SAMPLES))
    return [round(means[int(0.025 * BOOTSTRAP_SAMPLES)], 4), round(means[int(0.975 * BOOTSTRAP_SAMPLES) - 1], 4)]


def classify_errors(labels: dict[str, dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Why each wrong decision was wrong, in the order the pipeline can fail.

    extraction: a field the decision depends on was read wrongly (any field differs);
    retrieval: fields right, but the governing clause was not cited;
    reasoning: fields right and the governing clause cited, yet the decision is wrong.
    A heuristic: a decision can be wrong for more than one reason; the first applies.
    """
    out: dict[str, list[str]] = {"extraction": [], "retrieval": [], "reasoning": []}
    for claim_id, label in labels.items():
        pred = preds.get(claim_id)
        if pred is None or pred.get("decision") == label["decision"]:
            continue
        fields_ok = all(
            metrics.field_correct(f, label["fields"][f], pred.get("fields", {}).get(f))
            for f in metrics.ALL_FIELDS
            if f in label["fields"]
        )
        governing = set(label["governing_clause_ids"])
        cited = set(pred.get("cited_clause_ids", []))
        if not fields_ok:
            out["extraction"].append(claim_id)
        elif not governing <= cited:
            out["retrieval"].append(claim_id)
        else:
            out["reasoning"].append(claim_id)
    return out


def dangerous(labels: dict[str, dict[str, Any]], preds: dict[str, dict[str, Any]]) -> list[str]:
    """Claims the label says to deny or escalate, or that carry a fraud flag, recommended approve."""
    return sorted(
        c
        for c, label in labels.items()
        if c in preds
        and preds[c].get("decision") == "approve"
        and (label["decision"] != "approve" or label["fraud_flags"])
    )


def provenance() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()

    config = (REPO / "backend" / "app" / "config.py").read_text()
    models = dict(re.findall(r'(llm_(?:fast|strong)_model): str = "([^"]+)"', config))
    latest: dict[str, str] = {}
    for path in sorted((REPO / "backend" / "app" / "pipeline" / "prompts").glob("*.v*.md")):
        name, version = path.stem.rsplit(".", 1)
        latest[name] = version  # sorted, so the highest version wins
    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(git("status", "--porcelain", "--", ":!ClaimPilot*")),
        "models": models,
        "prompt_versions": latest,
        "frozen_test_set_unchanged": testset_unchanged(FROZEN_DATA),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="test")
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--retrieval", type=Path, help="predictions from `evalcli retrieve` for recall@5")
    ap.add_argument("--name", required=True)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    labels = load_labels(args.split)
    preds = json.loads(args.predictions.read_text())
    report = score(labels, preds)
    report["split"] = args.split
    report["note"] = args.note
    report["provenance"] = provenance()

    scored = [c for c in labels if c in preds]
    report["intervals_95"] = {
        "decision_accuracy": interval([float(preds[c].get("decision") == labels[c]["decision"]) for c in scored]),
        "extraction_macro": interval(
            [
                sum(
                    metrics.field_correct(f, labels[c]["fields"][f], preds[c].get("fields", {}).get(f))
                    for f in metrics.ALL_FIELDS
                    if f in labels[c]["fields"]
                )
                / sum(1 for f in metrics.ALL_FIELDS if f in labels[c]["fields"])
                for c in scored
            ]
        ),
    }
    errors = classify_errors(labels, preds)
    report["error_taxonomy"] = {k: {"count": len(v), "claims": v} for k, v in errors.items()}
    report["dangerous_quadrant"] = {
        "approved_but_should_not_be": dangerous(labels, preds),
        "count": len(dangerous(labels, preds)),
    }
    cost = [p["cost_usd"] for p in preds.values() if "cost_usd" in p]
    if cost:
        ordered = sorted(cost)
        report["cost_per_claim_usd"] = {
            "mean": round(sum(cost) / len(cost), 6),
            "p90": round(ordered[int(0.9 * len(ordered)) - 1], 6),
            "note": "estimated from assumed list prices; analysis stages only (extraction is separate)",
        }
    if args.retrieval:
        rpreds = json.loads(args.retrieval.read_text())
        ranked = {c: [x["clause_id"] for x in p["candidates"]] for c, p in rpreds.items() if "candidates" in p}
        if ranked:
            hits = [
                float(bool(set(labels[c]["governing_clause_ids"]) & set(ids[:5])))
                for c, ids in ranked.items()
                if c in labels
            ]
            report["retrieval_recall_at_5"] = {"value": round(sum(hits) / len(hits), 4), "claims": len(hits)}

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.name}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"extraction", "decision", "fraud", "citations"}}, indent=2
        )[:3500]
    )
    print(
        f"decision accuracy {report['decision']['accuracy']:.3f}  extraction macro {report['extraction']['macro']:.3f}"
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
