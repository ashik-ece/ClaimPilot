"""Turn raw pilot sessions into the three pilot numbers, with the rules stated in the output.

    uv run python -m pilot.analyze pilot/raw/sessions_v1.0.csv [more.csv ...] \
        --feedback pilot/raw/feedback_v1.0.csv --out results/pilot_v1.0.json

Rules (also printed with the results):
  - the first claim of each tester in each round is dropped as a learning claim;
  - sessions whose longest idle gap exceeds 5 minutes are dropped as interrupted;
  - time is the median of seconds from first open to submitted decision, per mode, with N;
  - per-tester medians are shown beside the pooled median, never replaced by it;
  - override rate counts only AI-assisted decisions (a manual reviewer saw no recommendation).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

IDLE_LIMIT_SECONDS = 300
BOOTSTRAP_SAMPLES = 10_000
MODES = ("assisted", "manual")


def read_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(newline="") as handle:
            rows += list(csv.DictReader(handle))
    for r in rows:
        r["position"] = int(r["position"])
        r["round"] = int(r["round"])
        r["overridden"] = r["overridden"] == "1"
        r["review_seconds"] = float(r["review_seconds"]) if r["review_seconds"] else None
        r["longest_idle_seconds"] = int(r["longest_idle_seconds"]) if r["longest_idle_seconds"] != "" else None
    return rows


def split_timing_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the exclusion rules; return the rows that count and how many each rule removed."""
    kept: list[dict[str, Any]] = []
    dropped = Counter()
    for r in rows:
        if r["review_seconds"] is None:
            dropped["no_timing"] += 1
        elif r["position"] == 1:
            dropped["learning_claim"] += 1
        elif r["longest_idle_seconds"] is None or r["longest_idle_seconds"] > IDLE_LIMIT_SECONDS:
            dropped["interrupted_or_idle_unknown"] += 1
        else:
            kept.append(r)
    return kept, dict(dropped)


def bootstrap_reduction(assisted: list[float], manual: list[float], seed: int = 20261024) -> tuple[float, float] | None:
    """95% interval for 1 - median(assisted)/median(manual), resampling claims within each mode."""
    if len(assisted) < 2 or len(manual) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP_SAMPLES):
        a = statistics.median(rng.choices(assisted, k=len(assisted)))
        m = statistics.median(rng.choices(manual, k=len(manual)))
        draws.append(1 - a / m)
    draws.sort()
    return draws[int(0.025 * BOOTSTRAP_SAMPLES)], draws[int(0.975 * BOOTSTRAP_SAMPLES) - 1]


def timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept, dropped = split_timing_rows(rows)
    by_mode: dict[str, list[float]] = {m: [r["review_seconds"] for r in kept if r["mode"] == m] for m in MODES}
    per_tester: dict[str, dict[str, Any]] = defaultdict(dict)
    for tester in sorted({r["tester"] for r in kept}):
        for m in MODES:
            xs = [r["review_seconds"] for r in kept if r["tester"] == tester and r["mode"] == m]
            per_tester[tester][m] = {"n": len(xs), "median_s": round(statistics.median(xs), 1) if xs else None}
    out: dict[str, Any] = {
        "dropped": dropped,
        "n": {m: len(by_mode[m]) for m in MODES},
        "median_s": {m: round(statistics.median(by_mode[m]), 1) if by_mode[m] else None for m in MODES},
        "per_tester": dict(per_tester),
    }
    if by_mode["assisted"] and by_mode["manual"]:
        med_a, med_m = statistics.median(by_mode["assisted"]), statistics.median(by_mode["manual"])
        out["relative_reduction"] = round(1 - med_a / med_m, 4)
        ci = bootstrap_reduction(by_mode["assisted"], by_mode["manual"])
        out["relative_reduction_ci95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
    return out


def decisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assisted = [r for r in rows if r["mode"] == "assisted"]
    overrides = [r for r in assisted if r["overridden"]]
    out: dict[str, Any] = {
        "assisted_decisions": len(assisted),
        "overrides": len(overrides),
        "override_rate": round(len(overrides) / len(assisted), 4) if assisted else None,
        "override_reasons": dict(Counter(r["reason_code"] or "none" for r in overrides)),
        "override_notes": [r["note"] for r in overrides if r["note"]],
    }
    for m in MODES:
        subset = [r for r in rows if r["mode"] == m and r["gold_decision"]]
        want = {"approve": "APPROVED", "deny": "DENIED", "escalate": "ESCALATED"}
        right = sum(want[r["gold_decision"]] == r["outcome"] for r in subset)
        out[f"agreement_with_label_{m}"] = {"n": len(subset), "correct": right}
    ai = [r for r in rows if r["ai_recommendation"] and r["gold_decision"]]
    out["ai_agreement_with_label_same_claims"] = {
        "n": len(ai),
        "correct": sum(r["ai_recommendation"] == r["gold_decision"] for r in ai),
    }
    # The dangerous case: a human approved a claim the label says to deny or escalate.
    out["labelled_non_approve_but_approved"] = [
        f"{r['tester']}:{r['source_claim']}:{r['mode']}"
        for r in rows
        if r["outcome"] == "APPROVED" and r["gold_decision"] in {"deny", "escalate"}
    ]
    return out


def feedback(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None
    return {
        "n": len(rows),
        "usefulness": [int(r["usefulness"]) for r in rows],
        "trust": [int(r["trust"]) for r in rows],
        "mean_usefulness": round(statistics.mean(int(r["usefulness"]) for r in rows), 2),
        "mean_trust": round(statistics.mean(int(r["trust"]) for r in rows), 2),
        "top_complaints": [r["top_complaint"] for r in rows if r["top_complaint"]],
        "top_missing": [r["top_missing"] for r in rows if r["top_missing"]],
    }


def analyze(rows: list[dict[str, Any]], feedback_path: Path | None = None) -> dict[str, Any]:
    simulated = any(r["tester"].startswith("SIM-") or r["release"] == "SIMULATED" for r in rows)
    return {
        "simulated": simulated,
        "warning": (
            "SIMULATED DATA. Times and choices were drawn from assumed distributions, so these "
            "numbers describe the assumptions, not the tool or any adjuster."
            if simulated
            else None
        ),
        "rules": {
            "learning_claim": "position 1 of each tester in each round is dropped",
            "interruption": f"sessions with a longest idle gap over {IDLE_LIMIT_SECONDS}s are dropped",
            "statistic": "median seconds per mode; 95% bootstrap interval over claims",
            "override_rate": "AI-assisted decisions only",
        },
        "testers": sorted({r["tester"] for r in rows}),
        "decisions_total": len(rows),
        "timing": timing(rows),
        "decisions": decisions(rows),
        "feedback": feedback(feedback_path),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--feedback", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    report = analyze(read_rows(args.sessions), args.feedback)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
    t = report["timing"]
    if report["simulated"]:
        print("*** " + report["warning"] + " ***")
    print(f"testers {len(report['testers'])}, decisions {report['decisions_total']}, dropped {t['dropped']}")
    print(f"N assisted {t['n']['assisted']}, manual {t['n']['manual']}; median s {t['median_s']}")
    if "relative_reduction" in t:
        print(f"relative reduction {t['relative_reduction']:.1%}, 95% interval {t['relative_reduction_ci95']}")
    print(f"override rate {report['decisions']['override_rate']} {report['decisions']['override_reasons']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
