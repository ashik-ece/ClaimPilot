"""Pilot tooling: assign counterbalanced tasks to testers, and export the raw sessions.

    python -m app.pilot setup --testers a@x.edu b@y.edu c@z.edu --round 1 \\
        --extractions ../eval/results/preds_extract_test.json
    python -m app.pilot export --out ../eval/pilot/raw/sessions_v1.0.csv

Each tester gets their own copy of every claim they review (a claim can only be decided once).
A copy is seeded from the frozen test set: the documents are uploaded, the stored extraction is
attached, and the real analysis code produces the recommendation, using the recorded model
responses (LLM_MODE=replay), so what a tester sees is what the offline evaluation scored.
"""

import argparse
import csv
import hashlib
import json
import random
import secrets
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import audit
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Claim,
    Decision,
    Document,
    Extraction,
    PilotAssignment,
    PilotFeedback,
    Policy,
    Recommendation,
    ReviewSession,
    User,
)
from app.pipeline.analysis import analyze_claim
from app.pipeline.llm import get_llm_client
from app.pipeline.policy_index import FastEmbedder, PolicyIndex
from app.pipeline.stages import ensure_indexed, persist_analysis
from app.security import hash_password
from app.states import ClaimStatus
from app.storage import S3Storage, Storage

DATA = Path(__file__).resolve().parents[2] / "eval" / "data"
ACTOR = "system:pilot"
PER_TYPE = 4  # claims per decision type in a round's pool: 12 claims in all
HARD_PER_TYPE = 2


def is_hard(label: dict[str, Any]) -> bool:
    """Difficulty from the labels only: degraded scans, ambiguity, distractors or fraud flags."""
    scenario = label["scenario"]
    return bool(
        label["doc_quality"]["tier"] != "clean"
        or scenario.get("ambiguity")
        or scenario.get("distractor")
        or label["fraud_flags"]
    )


def choose_pool(
    labels: dict[str, dict[str, Any]],
    seed: int,
    exclude: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[str]:
    """12 claims: 4 per decision type, half hard half easy. Chosen without looking at the AI."""
    rng = random.Random(seed)
    pool: list[str] = []
    for decision in ("approve", "deny", "escalate"):
        for hard in (True, False):
            ids = sorted(
                i
                for i, label in labels.items()
                if label["decision"] == decision and is_hard(label) == hard and i not in exclude
            )
            if len(ids) < HARD_PER_TYPE:
                raise SystemExit(f"not enough {'hard' if hard else 'easy'} {decision} claims")
            pool += rng.sample(ids, HARD_PER_TYPE)
    return sorted(pool)


def plan(pool: Sequence[str], testers: int, seed: int) -> list[list[tuple[str, str]]]:
    """Per tester, the ordered (claim, mode) list.

    Mode alternates by (tester + claim index), so every claim is reviewed in both modes across
    testers and every tester gets half of each. The first claim of each tester (dropped from the
    timing analysis as a learning claim) alternates between modes across testers.
    """
    out: list[list[tuple[str, str]]] = []
    for t in range(testers):
        items = [(c, "assisted" if (t + j) % 2 == 0 else "manual") for j, c in enumerate(pool)]
        random.Random(seed * 1000 + t).shuffle(items)
        first_mode = "assisted" if t % 2 == 0 else "manual"
        k = next(i for i, (_, m) in enumerate(items) if m == first_mode)
        items[0], items[k] = items[k], items[0]
        out.append(items)
    return out


def _load_labels(split: str = "test") -> dict[str, dict[str, Any]]:
    ids = json.loads((DATA / "splits.json").read_text())[split]
    return {i: json.loads((DATA / "claims" / f"{i}.json").read_text()) for i in ids}


def _tester_user(session: Session, email: str) -> tuple[User, str | None]:
    user = session.scalar(select(User).where(User.email == email))
    if user is not None:
        return user, None
    password = secrets.token_urlsafe(9)
    user = User(email=email, password_hash=hash_password(password), role="adjuster")
    session.add(user)
    session.flush()
    audit.record(session, actor=ACTOR, event_type="user.created", payload={"user_id": str(user.id)})
    return user, password


def seed_claim(
    session: Session,
    storage: Storage,
    label: dict[str, Any],
    extraction: dict[str, Any],
    analysis_for: Any,
    created_by: Any,
) -> Claim:
    claim = Claim(
        claim_type="auto_collision", status=ClaimStatus.RECEIVED.value, created_by=created_by
    )
    session.add(claim)
    session.flush()
    audit.record(
        session,
        actor=ACTOR,
        event_type="claim.created",
        claim_id=claim.id,
        payload={"pilot_source": label["claim_id"]},
    )
    for spec in label["documents"]:
        data = (DATA / "documents" / label["claim_id"] / spec["filename"]).read_bytes()
        doc_id = uuid.uuid4()
        key = f"claims/{claim.id}/{doc_id}/{spec['filename']}"
        storage.put(key, data, "application/pdf")
        session.add(
            Document(
                id=doc_id,
                claim_id=claim.id,
                filename=spec["filename"],
                content_type="application/pdf",
                s3_key=key,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                uploaded_by=created_by,
            )
        )
    meta = {
        k: extraction.get(k)
        for k in ("needs_review", "issues", "unverified_fields", "vision_sourced", "used_vision")
    }
    session.execute(
        insert(Extraction).values(
            claim_id=claim.id,
            fields=extraction["fields"],
            meta=meta,
            prompt_version="extraction@v2",
        )
    )
    claim.claimant_name = extraction["fields"].get("claimant_name")
    amount = extraction["fields"].get("total_amount")
    try:
        claim.amount = Decimal(str(amount)) if amount not in (None, "") else None
    except InvalidOperation:
        claim.amount = None
    claim.status = ClaimStatus.EXTRACTED.value
    session.flush()
    persist_analysis(session, claim, analysis_for)
    return claim


def setup(args: argparse.Namespace) -> None:
    labels = _load_labels(args.split)
    extractions: dict[str, Any] = json.loads(args.extractions.read_text())
    labels = {i: v for i, v in labels.items() if i in extractions}
    prior = set(args.exclude or [])
    pool = choose_pool(labels, args.seed + args.round, prior)
    schedule = plan(pool, len(args.testers), args.seed + args.round)
    llm = get_llm_client()
    index = PolicyIndex(QdrantClient(url=settings.qdrant_url), FastEmbedder())
    storage = S3Storage()
    credentials: list[tuple[str, str]] = []

    with SessionLocal() as session:
        policies_seen: set[Any] = set()
        for known in session.scalars(select(Policy)):
            ensure_indexed(session, index, known, policies_seen)
        # Analyse each pool claim once, exactly as the offline run did (earlier = lower ids).
        analyses = {}
        for claim_id in pool:
            earlier = [(i, extractions[i]["fields"]) for i in sorted(extractions) if i < claim_id]
            ext = extractions[claim_id]
            analyses[claim_id] = analyze_claim(
                session,
                index,
                llm,
                ext["fields"],
                extraction_ok=not ext["needs_review"],
                extraction_issues=ext["issues"],
                earlier=earlier,
            )
            if any("model unavailable" in n for n in analyses[claim_id].recommendation.notes):
                raise SystemExit(f"{claim_id}: model unavailable; use LLM_MODE=replay or record")

        for t, (email, tasks) in enumerate(zip(args.testers, schedule, strict=True), start=1):
            user, password = _tester_user(session, email)
            if password:
                credentials.append((email, password))
            for position, (claim_id, mode) in enumerate(tasks, start=1):
                claim = seed_claim(
                    session,
                    storage,
                    labels[claim_id],
                    extractions[claim_id],
                    analyses[claim_id],
                    user.id,
                )
                session.add(
                    PilotAssignment(
                        user_id=user.id,
                        claim_id=claim.id,
                        tester=f"T{t}",
                        source_claim=claim_id,
                        mode=mode,
                        position=position,
                        round=args.round,
                        release=settings.release,
                    )
                )
        session.commit()

    print(f"round {args.round}: pool {', '.join(pool)}")
    for t, tasks in enumerate(schedule, start=1):
        print(f"  T{t}: " + " ".join(f"{c}:{m[0]}" for c, m in tasks))
    for email, password in credentials:
        print(f"new account {email} password {password}  (shown once)")


def export(args: argparse.Namespace) -> None:
    """One row per decision made on a pilot claim, with no email or other identifier."""
    fields = [
        "release", "round", "tester", "position", "source_claim", "mode", "review_seconds",
        "longest_idle_seconds", "outcome", "reason_code", "overridden", "note", "ai_recommendation",
        "gold_decision", "started_at", "decided_at",
    ]  # fmt: skip
    gold = {i: v["decision"] for split in ("test", "dev") for i, v in _load_labels(split).items()}
    with SessionLocal() as session:
        rows = session.execute(
            select(PilotAssignment, Decision, ReviewSession)
            .join(Decision, Decision.claim_id == PilotAssignment.claim_id)
            .outerjoin(ReviewSession, ReviewSession.id == Decision.review_session_id)
            .order_by(PilotAssignment.tester, PilotAssignment.round, PilotAssignment.position)
        ).all()
        recs = {r.claim_id: r.outcome for r in session.scalars(select(Recommendation))}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for a, d, s in rows:
                writer.writerow(
                    {
                        "release": a.release, "round": a.round, "tester": a.tester,
                        "position": a.position, "source_claim": a.source_claim, "mode": d.review_mode or a.mode,
                        "review_seconds": round(d.review_seconds, 1) if d.review_seconds is not None else "",
                        "longest_idle_seconds": d.longest_idle_seconds if d.longest_idle_seconds is not None else "",
                        "outcome": d.outcome, "reason_code": d.reason_code or "",
                        "overridden": int(d.overridden), "note": (d.note or "").replace("\n", " "),
                        "ai_recommendation": recs.get(a.claim_id, ""),
                        "gold_decision": gold.get(a.source_claim, ""),
                        "started_at": s.started_at.isoformat() if s else "",
                        "decided_at": d.created_at.isoformat(),
                    }
                )  # fmt: skip
        print(f"wrote {args.out} ({len(rows)} decisions)")
        if args.feedback_out:
            export_feedback(session, args.feedback_out)


def export_feedback(session: Session, out: Path) -> None:
    """Feedback with the tester label instead of an email."""
    label = {
        user_id: tester
        for user_id, tester in session.execute(
            select(PilotAssignment.user_id, PilotAssignment.tester).distinct()
        )
    }
    rows = session.scalars(select(PilotFeedback).order_by(PilotFeedback.created_at)).all()
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["release", "tester", "usefulness", "trust", "top_complaint", "top_missing"]
        )
        for f in rows:
            writer.writerow(
                [
                    f.release, label.get(f.user_id, "untracked"), f.usefulness, f.trust,
                    (f.top_complaint or "").replace("\n", " "), (f.top_missing or "").replace("\n", " "),
                ]
            )  # fmt: skip
    print(f"wrote {out} ({len(rows)} responses)")


# Assumptions for the dry run. They are stated so nobody mistakes the output for a finding: the
# simulated times and choices are drawn from these numbers, so the results only echo them.
SIM = {
    "manual_median_seconds": 300,
    "assisted_time_factor": 0.6,
    "time_sigma": 0.35,
    "interruption_rate": 0.05,
    "follow_ai_rate": 0.88,
    "manual_correct_rate": 0.9,
    "assisted_override_correct_rate": 0.7,
}


def simulate(args: argparse.Namespace) -> None:
    """Write a SIMULATED sessions file in the real export format, to dry-run the analysis.

    Real pool and assignment plan, real AI predictions; the human behaviour is invented from the
    assumptions in SIM. Tester labels start with SIM- and the release is SIMULATED, so downstream
    tools can never mistake it for real data.
    """
    import math

    labels = _load_labels("test")
    preds = json.loads(args.predictions.read_text())
    labels = {i: v for i, v in labels.items() if i in preds}
    pool = choose_pool(labels, args.seed + 1)
    schedule = plan(pool, args.testers, args.seed + 1)
    rng = random.Random(args.seed)
    outcome_of = {"approve": "APPROVED", "deny": "DENIED", "escalate": "ESCALATED"}
    reasons = ["wrong_clause", "coverage_interpretation", "missed_fraud_signal", "misread_document"]
    rows = []
    for t, tasks in enumerate(schedule, start=1):
        for position, (claim_id, mode) in enumerate(tasks, start=1):
            gold, ai = labels[claim_id]["decision"], preds[claim_id]["decision"]
            seconds = SIM["manual_median_seconds"] * math.exp(rng.gauss(0, SIM["time_sigma"]))
            if mode == "assisted":
                seconds *= SIM["assisted_time_factor"]
            if position == 1:
                seconds *= 1.5  # warm-up claim
            idle = 400 if rng.random() < SIM["interruption_rate"] else rng.randint(2, 60)
            overridden, reason, note = False, "", ""
            if mode == "assisted":
                if rng.random() < SIM["follow_ai_rate"]:
                    decision = ai
                else:
                    # An override is by definition a different answer from the AI's.
                    others = [d for d in ("approve", "deny", "escalate") if d != ai]
                    right = gold != ai and rng.random() < SIM["assisted_override_correct_rate"]
                    decision = gold if right else rng.choice(others)
                overridden = decision != ai
                if overridden:
                    reason, note = rng.choice(reasons), "SIMULATED note"
            else:
                decision = (
                    gold
                    if rng.random() < SIM["manual_correct_rate"]
                    else rng.choice(["approve", "deny", "escalate"])
                )
            rows.append({
                "release": "SIMULATED", "round": 1, "tester": f"SIM-T{t}", "position": position,
                "source_claim": claim_id, "mode": mode, "review_seconds": round(seconds, 1),
                "longest_idle_seconds": idle, "outcome": outcome_of[decision], "reason_code": reason,
                "overridden": int(overridden), "note": note, "ai_recommendation": ai,
                "gold_decision": gold, "started_at": "", "decided_at": "",
            })  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    feedback = args.out.with_name(args.out.stem + "_feedback.csv")
    with feedback.open("w", newline="") as handle:
        plain = csv.writer(handle)
        plain.writerow(["release", "tester", "usefulness", "trust", "top_complaint", "top_missing"])
        for t in range(1, args.testers + 1):
            plain.writerow(
                [
                    "SIMULATED",
                    f"SIM-T{t}",
                    rng.randint(3, 5),
                    rng.randint(3, 4),
                    "SIMULATED",
                    "SIMULATED",
                ]
            )
    print(f"wrote {args.out} and {feedback.name}: SIMULATED, assumptions {SIM}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--testers", nargs="+", required=True, help="tester emails")
    p.add_argument("--round", type=int, default=1)
    p.add_argument(
        "--split", default="test", choices=["test", "dev"], help="dev is for rehearsals only"
    )
    p.add_argument("--seed", type=int, default=20261024)
    p.add_argument("--extractions", type=Path, required=True)
    p.add_argument("--exclude", nargs="*", help="claim ids already used in an earlier round")
    p.set_defaults(func=setup)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--feedback-out", type=Path)
    e.set_defaults(func=export)
    m = sub.add_parser("simulate", help="write a clearly labelled SIMULATED sessions file")
    m.add_argument("--predictions", type=Path, required=True)
    m.add_argument("--testers", type=int, default=4)
    m.add_argument("--seed", type=int, default=11, help="arbitrary; nothing was tuned to it")
    m.add_argument("--out", type=Path, required=True)
    m.set_defaults(func=simulate)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
