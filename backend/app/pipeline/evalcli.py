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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["extract"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--vision", default="auto", choices=["auto", "never", "always"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

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
        }
        print(claim_id, "review" if result.needs_review else "ok", result.issues, flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(preds, indent=1, sort_keys=True))
    print(f"wrote {args.out}; estimated cost ${total_cost:.4f}")


if __name__ == "__main__":
    main()
