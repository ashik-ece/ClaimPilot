"""Ingest the dataset through the ClaimPilot API (doubles as demo and load-test data).

    uv run python -m dataset.load --email admin@claimpilot.local --password '...' [--split test|dev|all]

Policies and clauses are created once; claims are created in id order (so duplicates arrive after
the claim they duplicate). A local id map makes re-runs skip claims that were already loaded.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

DATA = Path(__file__).resolve().parent.parent / "data"
ID_MAP = DATA.parent / ".cache" / "load_map.json"


def login(client: httpx.Client, email: str, password: str) -> None:
    r = client.post("/auth/login", data={"username": email, "password": password})
    r.raise_for_status()
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def ensure_policies(client: httpx.Client, data: Path = DATA) -> dict[str, str]:
    """Create each policy version and its clauses if missing; return policy code -> API id."""
    existing = {(p["policy_number"], p["version"]): p["id"] for p in client.get("/policies").json()}
    ids: dict[str, str] = {}
    for path in sorted((data / "policies").glob("*.json")):
        meta = json.loads(path.read_text())
        key = (meta["policy_number"], meta["version"])
        if key not in existing:
            r = client.post(
                "/policies",
                json={
                    "policy_number": meta["policy_number"],
                    "version": meta["version"],
                    "name": meta["name"],
                    "effective_date": meta["effective_date"],
                    "expiry_date": meta["expiry_date"],
                },
            )
            r.raise_for_status()
            existing[key] = r.json()["id"]
        pid = existing[key]
        if not client.get(f"/policies/{pid}/clauses").json():
            clauses = [
                {"clause_id": c["clause_id"], "section_path": c["section_path"], "text": c["text"]}
                for c in meta["clauses"]
            ]
            client.post(f"/policies/{pid}/clauses", json=clauses).raise_for_status()
        ids[meta["code"]] = pid
    return ids


def load_claim(client: httpx.Client, claim: dict[str, Any], policy_id: str, data: Path = DATA) -> str:
    f = claim["fields"]
    r = client.post(
        "/claims",
        json={
            "claimant_name": f["claimant_name"],
            "claim_type": "auto_collision",
            "amount": f["total_amount"],
            "policy_id": policy_id,
        },
    )
    r.raise_for_status()
    api_id: str = r.json()["id"]
    for doc in claim["documents"]:
        path = data / "documents" / claim["claim_id"] / doc["filename"]
        up = client.post(
            f"/claims/{api_id}/documents",
            files={"file": (doc["filename"], path.read_bytes(), "application/pdf")},
        )
        up.raise_for_status()
    return api_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="load only the first N claims")
    args = ap.parse_args()

    splits = json.loads((DATA / "splits.json").read_text())
    wanted = sorted(splits["dev"] + splits["test"]) if args.split == "all" else splits[args.split]
    if args.limit:
        wanted = wanted[: args.limit]
    loaded: dict[str, str] = json.loads(ID_MAP.read_text()) if ID_MAP.exists() else {}

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        login(client, args.email, args.password)
        policy_ids = ensure_policies(client)
        started, new = time.time(), 0
        for cid in wanted:
            if cid in loaded:
                continue
            claim = json.loads((DATA / "claims" / f"{cid}.json").read_text())
            loaded[cid] = load_claim(client, claim, policy_ids[claim["policy_code"]])
            new += 1
            ID_MAP.parent.mkdir(exist_ok=True)
            ID_MAP.write_text(json.dumps(loaded, indent=2))
    print(f"loaded {new} new claims ({len(loaded)} total mapped) in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
