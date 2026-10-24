"""Run extraction over a dataset split, writing predictions for `eval/harness`.

    uv run python -m app.pipeline.evalcli extract --split dev --vision auto --out ../eval/results/preds_extract_dev.json

Reads documents straight from eval/data (no database), so it measures the agent in isolation.
"""

import argparse
import json
from pathlib import Path

from app.pipeline.extraction import extract_claim
from app.pipeline.llm import LLMError, QuotaExhausted, get_llm_client
from app.pipeline.ocr import PageText, read_pdf

DATA = Path(__file__).resolve().parents[3] / "eval" / "data"


def retrieve_main(args: argparse.Namespace) -> None:
    from datetime import date

    from qdrant_client import QdrantClient
    from sqlalchemy import select

    from app.config import settings
    from app.db import SessionLocal
    from app.models import Policy, PolicyClause
    from app.pipeline.policy_index import Clause, FastEmbedder, PolicyIndex
    from app.pipeline.policy_lookup import resolve_policy
    from app.pipeline.retrieval import retrieve

    index = PolicyIndex(QdrantClient(url=settings.qdrant_url), FastEmbedder())
    extractions = json.loads(args.extractions.read_text())
    preds: dict[str, dict[str, object]] = {}
    with SessionLocal() as session:
        for known in session.scalars(select(Policy)):
            rows = session.scalars(select(PolicyClause).where(PolicyClause.policy_id == known.id))
            index.index_policy(
                known.policy_number,
                known.version,
                [Clause(r.clause_id, r.section_path, r.text) for r in rows],
            )
        for claim_id, ext in extractions.items():
            facts = ext["fields"]
            incident = (
                date.fromisoformat(facts["incident_date"]) if facts.get("incident_date") else None
            )
            policy = resolve_policy(session, facts.get("policy_number"), incident)
            if policy is None:
                print(claim_id, "UNKNOWN POLICY", facts.get("policy_number"), flush=True)
                continue
            flags: list[str] = []
            if args.flags == "fraud":
                flags = json.loads(args.fraud.read_text())[claim_id]["fraud_flags"]
            elif args.flags == "oracle":
                meta = json.loads((DATA / "claims" / f"{claim_id}.json").read_text())
                flags = [f if isinstance(f, str) else f["type"] for f in meta["fraud_flags"]]
            result = retrieve(
                index,
                policy_number=policy.policy_number,
                version=policy.version,
                facts=facts,
                narrative=facts.get("incident_description"),
                llm=get_llm_client(),
                flags=flags,
            )
            preds[claim_id] = {
                "cited_clause_ids": [c.clause_id for c in result.candidates],
                "candidates": [
                    {"clause_id": c.clause_id, "score": round(c.score, 3), "reasons": c.reasons}
                    for c in result.candidates
                ],
                "checks": result.checks,
                "terms": {
                    "high_value_threshold": str(result.terms.high_value_threshold)
                    if result.terms.high_value_threshold is not None
                    else None,
                    "notice_days": result.terms.notice_days,
                },
                "rejected_picks": result.rejected,
                "selection": result.selection,
                "cost_usd": sum(c.cost_usd for c in result.calls),
            }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(preds, indent=1, sort_keys=True))
    print(f"wrote {args.out}")


def fraud_main(args: argparse.Namespace) -> None:
    from decimal import Decimal

    from app.pipeline import fraud
    from app.pipeline.terms import PolicyTerms

    extractions = json.loads(args.extractions.read_text())
    retrieval = json.loads(args.retrieval.read_text())
    preds: dict[str, dict[str, object]] = {}
    for claim_id in sorted(extractions):  # claim ids are in arrival order
        if claim_id not in retrieval:
            continue
        threshold = retrieval[claim_id]["terms"]["high_value_threshold"]
        terms = PolicyTerms(None, None, None, Decimal(threshold) if threshold else None, {})
        earlier = [(i, extractions[i]["fields"]) for i in sorted(extractions) if i < claim_id]
        result = fraud.evaluate(
            extractions[claim_id]["fields"], terms, retrieval[claim_id]["checks"], earlier
        )
        preds[claim_id] = {
            "fraud_flags": result.types,
            "details": [f.detail for f in result.flags],
            "unchecked": result.unchecked,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(preds, indent=1, sort_keys=True))
    print(f"wrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["extract", "retrieve", "fraud"])
    ap.add_argument("--extractions", type=Path, help="predictions file from the extract command")
    ap.add_argument("--flags", default="none", choices=["none", "oracle", "fraud"])
    ap.add_argument("--fraud", type=Path, help="predictions file from the fraud command")
    ap.add_argument("--retrieval", type=Path, help="predictions file from the retrieve command")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--vision", default="auto", choices=["auto", "never", "always"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if args.command == "fraud":
        fraud_main(args)
        return
    if args.command == "retrieve":
        retrieve_main(args)
        return
    ids = json.loads((DATA / "splits.json").read_text())[args.split][: args.limit]
    client = get_llm_client()
    preds: dict[str, dict[str, object]] = {}
    total_cost = 0.0
    for claim_id in ids:
        meta = json.loads((DATA / "claims" / f"{claim_id}.json").read_text())
        pages: list[PageText] = []
        for doc in meta["documents"]:
            path = DATA / "documents" / claim_id / doc["filename"]
            pages += read_pdf(path.read_bytes(), doc["filename"])
        try:
            result = extract_claim(pages, client, vision=args.vision)
        except QuotaExhausted:
            print(f"quota exhausted at {claim_id}; rerun later to resume from cache", flush=True)
            break
        except LLMError as exc:
            print(claim_id, "FAILED", str(exc)[:150], flush=True)
            continue
        total_cost += result.cost_usd
        preds[claim_id] = {
            "fields": result.fields,
            "needs_review": result.needs_review,
            "issues": result.issues,
            "unverified_fields": result.unverified_fields,
            "used_vision": result.used_vision,
            "vision_sourced": result.vision_sourced,
        }
        print(claim_id, "review" if result.needs_review else "ok", result.issues, flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(preds, indent=1, sort_keys=True))
    print(f"wrote {args.out}; estimated cost ${total_cost:.4f}")


if __name__ == "__main__":
    main()
