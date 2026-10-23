"""Retrieval baseline: is the labeled governing clause in the top 5 for a dev claim?

The query is built from the incident type and the claim narrative, with no OCR, so this
isolates retrieval quality from extraction quality. Phase 3 compares against this number.

    uv run python -m dataset.retrieval_smoke [--url http://localhost:6335]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from dataset import index_policies as ip

DATA = ip.DATA
RESULTS = DATA.parent / "results"


def build_query(claim: dict[str, Any]) -> str:
    return f"{claim['fields']['incident_type']}. {claim['narrative_text']}"


def evaluate(client: QdrantClient, claim_ids: list[str], data: Path = DATA, k: int = 5) -> dict[str, Any]:
    model = ip.embedder()
    per_claim = []
    for cid in claim_ids:
        claim = json.loads((data / "claims" / f"{cid}.json").read_text())
        policy = json.loads((data / "policies" / f"{claim['policy_code']}.json").read_text())
        hits = ip.search(client, model, build_query(claim), policy["policy_number"], policy["version"], k)
        got = [h["clause_id"] for h in hits]
        gold = claim["governing_clause_ids"]
        found = [g for g in gold if g in got]
        per_claim.append(
            {
                "claim_id": cid,
                "scenario": claim["scenario"]["type"],
                "gold": gold,
                "top_k": got,
                "any_hit": bool(found),
                "all_hit": len(found) == len(gold),
                "fraction_found": len(found) / len(gold),
            }
        )
    n = len(per_claim)
    by_scenario: dict[str, list[bool]] = {}
    for r in per_claim:
        by_scenario.setdefault(r["scenario"], []).append(r["any_hit"])
    return {
        "k": k,
        "claims": n,
        "recall_at_k_any": sum(r["any_hit"] for r in per_claim) / n,
        "recall_at_k_all": sum(r["all_hit"] for r in per_claim) / n,
        "mean_fraction_found": sum(r["fraction_found"] for r in per_claim) / n,
        "any_hit_by_scenario": {s: sum(v) / len(v) for s, v in sorted(by_scenario.items())},
        "per_claim": per_claim,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:6335")
    args = ap.parse_args()
    dev = json.loads((DATA / "splits.json").read_text())["dev"]
    client = QdrantClient(url=args.url)
    out = {
        "model": ip.MODEL,
        "dev_10": evaluate(client, dev[:10]),
        "dev_all": evaluate(client, dev),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "retrieval_baseline.json").write_text(json.dumps(out, indent=2) + "\n")
    for key in ("dev_10", "dev_all"):
        r = out[key]
        print(f"{key}: n={r['claims']} recall@5 any={r['recall_at_k_any']:.2f} all={r['recall_at_k_all']:.2f}")
    print("by scenario (any-hit, dev_all):", out["dev_all"]["any_hit_by_scenario"])
