"""Job handlers: persist what each agent produced, move the claim along, and audit everything.

Agents run as `agent:*` actors, which the state machine bars from terminal states, so the
pipeline can only ever leave a claim awaiting a human decision.
"""

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import audit
from app.config import settings
from app.models import (
    Claim,
    Document,
    Extraction,
    PipelineJob,
    Policy,
    PolicyClause,
    Recommendation,
)
from app.pipeline.analysis import analyze_claim
from app.pipeline.extraction import extract_claim
from app.pipeline.jobs import Handler
from app.pipeline.llm import LLMClient, LLMResult, record_model_call
from app.pipeline.ocr import PageText, read_pdf
from app.pipeline.policy_index import Clause, PolicyIndex
from app.pipeline.prompts import load_prompt
from app.states import ClaimStatus, IllegalTransition, check_transition
from app.storage import Storage

EXTRACTION_ACTOR = "agent:extraction"
ANALYSIS_ACTOR = "agent:analysis"


def _move(session: Session, claim: Claim, target: ClaimStatus, actor: str) -> None:
    previous = claim.status
    check_transition(ClaimStatus(previous), target, actor)
    claim.status = target.value
    audit.record(
        session,
        actor=actor,
        event_type="claim.transitioned",
        claim_id=claim.id,
        payload={"from": previous, "to": target.value},
    )


def _calls(session: Session, claim_id: uuid.UUID, actor: str, calls: list[LLMResult]) -> None:
    for call in calls:
        record_model_call(session, claim_id=claim_id, actor=actor, result=call)


def _amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value else None
    except InvalidOperation:
        return None


def run_extraction(session: Session, job: PipelineJob, storage: Storage, llm: LLMClient) -> str:
    claim = session.get(Claim, job.claim_id, with_for_update=True)
    if claim is None or claim.status != ClaimStatus.RECEIVED.value:
        raise RuntimeError(f"claim is not awaiting extraction (status {claim and claim.status})")
    docs = list(
        session.scalars(
            select(Document).where(Document.claim_id == claim.id).order_by(Document.created_at)
        )
    )
    pages: list[PageText] = []
    for doc in docs:
        if doc.content_type == "application/pdf" or doc.filename.lower().endswith(".pdf"):
            pages += read_pdf(storage.get(doc.s3_key), doc.filename)
    if not pages:
        raise RuntimeError("the claim has no readable PDF documents")

    result = extract_claim(pages, llm)
    _calls(session, claim.id, EXTRACTION_ACTOR, result.calls)
    meta = {
        "needs_review": result.needs_review,
        "issues": result.issues,
        "unverified_fields": result.unverified_fields,
        "vision_sourced": result.vision_sourced,
        "used_vision": result.used_vision,
    }
    prompt = load_prompt("extraction")
    session.execute(
        insert(Extraction)
        .values(
            claim_id=claim.id,
            fields=result.fields,
            meta=meta,
            prompt_version=f"{prompt.name}@{prompt.version}",
        )
        .on_conflict_do_update(
            index_elements=["claim_id"],
            set_={"fields": result.fields, "meta": meta},
        )
    )
    claim.claimant_name = result.fields.get("claimant_name") or claim.claimant_name
    claim.amount = _amount(result.fields.get("total_amount")) or claim.amount
    audit.record(
        session,
        actor=EXTRACTION_ACTOR,
        event_type="extraction.completed",
        claim_id=claim.id,
        payload={**meta, "documents": len(docs), "cost_usd": round(result.cost_usd, 6)},
    )
    _move(session, claim, ClaimStatus.EXTRACTED, EXTRACTION_ACTOR)
    return "analyze"


def ensure_indexed(
    session: Session, index: PolicyIndex, policy: Policy, seen: set[uuid.UUID]
) -> None:
    """Policy versions never change once loaded, so each is indexed once per process."""
    if policy.id in seen:
        return
    rows = session.scalars(select(PolicyClause).where(PolicyClause.policy_id == policy.id))
    index.index_policy(
        policy.policy_number,
        policy.version,
        [Clause(r.clause_id, r.section_path, r.text) for r in rows],
    )
    seen.add(policy.id)


def run_analysis(
    session: Session, job: PipelineJob, index: PolicyIndex, llm: LLMClient, seen: set[uuid.UUID]
) -> None:
    claim = session.get(Claim, job.claim_id, with_for_update=True)
    if claim is None or claim.status != ClaimStatus.EXTRACTED.value:
        raise RuntimeError(f"claim is not awaiting analysis (status {claim and claim.status})")
    extraction = session.scalar(select(Extraction).where(Extraction.claim_id == claim.id))
    if extraction is None:
        raise RuntimeError("no extraction to analyse")

    # Everything is indexed up front so any version of the policy can be searched.
    for known in session.scalars(select(Policy)):
        ensure_indexed(session, index, known, seen)
    earlier = [
        (str(row.claim_id), row.fields)
        for row in session.scalars(
            select(Extraction)
            .join(Claim, Claim.id == Extraction.claim_id)
            .where(Claim.created_at < claim.created_at, Claim.id != claim.id)
        )
    ]
    analysis = analyze_claim(
        session,
        index,
        llm,
        extraction.fields,
        extraction_ok=not extraction.meta.get("needs_review", False),
        extraction_issues=extraction.meta.get("issues", []),
        earlier=earlier,
    )
    rec = analysis.recommendation
    _calls(session, claim.id, ANALYSIS_ACTOR, rec.calls)
    if analysis.policy is not None:
        claim.policy_id = analysis.policy.id

    flags = (
        [{"type": f.type, "detail": f.detail, "evidence": f.evidence} for f in analysis.fraud.flags]
        if analysis.fraud
        else []
    )
    meta: dict[str, Any] = {
        "rules_outcome": rec.rules.outcome,
        "rules_reasons": rec.rules.reasons,
        "forced_by_weak_evidence": rec.rules.forced,
        "source": rec.source,
        "notes": rec.notes,
        "unchecked": analysis.fraud.unchecked if analysis.fraud else [],
        "checks": analysis.retrieval.checks if analysis.retrieval else {},
        "candidates": [
            {
                "clause_id": c.clause_id,
                "role": c.role,
                "score": round(c.score, 3),
                "reasons": c.reasons,
            }
            for c in (analysis.retrieval.candidates if analysis.retrieval else [])
        ],
    }
    prompt = load_prompt("recommendation")
    values = {
        "outcome": rec.outcome,
        "rationale": rec.rationale,
        "cited_clause_ids": rec.cited_clause_ids,
        "fraud_flags": flags,
        "meta": meta,
        "model_name": settings.llm_strong_model,
        "prompt_version": f"{prompt.name}@{prompt.version}",
    }
    session.execute(
        insert(Recommendation)
        .values(claim_id=claim.id, **values)
        .on_conflict_do_update(index_elements=["claim_id"], set_=values)
    )
    audit.record(
        session,
        actor=ANALYSIS_ACTOR,
        event_type="recommendation.created",
        claim_id=claim.id,
        payload={
            "outcome": rec.outcome,
            "cited_clause_ids": rec.cited_clause_ids,
            "fraud_flags": [f["type"] for f in flags],
            "source": rec.source,
        },
    )
    _move(session, claim, ClaimStatus.ANALYZED, ANALYSIS_ACTOR)
    _move(session, claim, ClaimStatus.AWAITING_ADJUDICATION, ANALYSIS_ACTOR)


def build_handlers(storage: Storage, llm: LLMClient, index: PolicyIndex) -> dict[str, Handler]:
    seen: set[uuid.UUID] = set()

    def extract(session: Session, job: PipelineJob) -> str | None:
        return run_extraction(session, job, storage, llm)

    def analyze(session: Session, job: PipelineJob) -> str | None:
        run_analysis(session, job, index, llm, seen)
        return None

    return {"extract": extract, "analyze": analyze}


def flag_for_review(session: Session, claim_id: uuid.UUID, stage: str, error: str) -> None:
    """A stage that exhausted its retries hands the claim to a human instead of stalling it."""
    claim = session.get(Claim, claim_id, with_for_update=True)
    if claim is None:
        return
    try:
        _move(session, claim, ClaimStatus.NEEDS_REVIEW, "system:pipeline")
    except IllegalTransition:
        return
    audit.record(
        session,
        actor="system:pipeline",
        event_type="pipeline.needs_review",
        claim_id=claim.id,
        payload={"stage": stage, "error": error[:500]},
    )


__all__ = ["build_handlers", "check_transition", "flag_for_review"]
