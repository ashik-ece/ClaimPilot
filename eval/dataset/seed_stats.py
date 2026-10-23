"""Distill distributions from the public seed dataset into data/seed_stats.json.

Source: Aqqad, A. "Insurance claims" (Mendeley Data, DOI 10.17632/992mh7dk9y), CC BY 4.0.
Only aggregate distributions are kept; no individual records are copied into the repo.

    uv run python -m dataset.seed_stats
"""

import csv
import io
import json
import statistics
from collections import Counter
from pathlib import Path

import httpx

SOURCE_URL = "https://raw.githubusercontent.com/mwitiderrick/insurancedata/master/insurance_claims.csv"
OUT = Path(__file__).resolve().parent.parent / "data" / "seed_stats.json"
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]


def _quantiles(values: list[float]) -> list[float]:
    values = sorted(values)
    return [values[min(len(values) - 1, int(q * len(values)))] for q in QUANTILES]


def build(rows: list[dict[str, str]]) -> dict[str, object]:
    collisions = [r for r in rows if r["incident_type"].endswith("Collision")]
    n = len(collisions)
    severity = Counter(r["incident_severity"] for r in collisions)
    collision_type = Counter(r["collision_type"] for r in collisions if r["collision_type"] != "?")
    return {
        "source": "Mendeley Data 10.17632/992mh7dk9y (CC BY 4.0)",
        "rows_total": len(rows),
        "rows_collision": n,
        "fraud_rate": round(sum(r["fraud_reported"] == "Y" for r in rows) / len(rows), 4),
        "incident_type_share": {k: round(v / n, 4) for k, v in Counter(r["incident_type"] for r in collisions).items()},
        "severity_share": {k: round(v / n, 4) for k, v in severity.items()},
        "collision_type_share": {k: round(v / sum(collision_type.values()), 4) for k, v in collision_type.items()},
        "vehicle_claim_quantiles": {
            "quantile_points": QUANTILES,
            "all": _quantiles([float(r["vehicle_claim"]) for r in collisions]),
            "by_severity": {
                sev: _quantiles([float(r["vehicle_claim"]) for r in collisions if r["incident_severity"] == sev])
                for sev in severity
            },
        },
        "vehicle_claim_median_by_severity": {
            sev: statistics.median(float(r["vehicle_claim"]) for r in collisions if r["incident_severity"] == sev)
            for sev in severity
        },
        "vehicles": sorted({(r["auto_make"], r["auto_model"]) for r in collisions}),
        "auto_year_range": [
            min(int(r["auto_year"]) for r in collisions),
            max(int(r["auto_year"]) for r in collisions),
        ],
    }


def main() -> None:
    text = httpx.get(SOURCE_URL, follow_redirects=True, timeout=60).text
    stats = build(list(csv.DictReader(io.StringIO(text))))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
