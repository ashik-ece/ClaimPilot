import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select

from app import audit
from app.config import settings
from app.deps import (
    AdminUser,
    SessionDep,
    StaffUser,
    actor_of,
    assigned_mode,
    forbid_in_manual_mode,
    get_claim_or_404,
)
from app.models import (
    AuditEvent,
    Claim,
    Decision,
    Document,
    Extraction,
    PilotAssignment,
    PipelineJob,
    PolicyClause,
    Recommendation,
    ReviewSession,
)
from app.pipeline import jobs
from app.schemas import (
    AuditEventOut,
    CitedClause,
    ClaimCreate,
    ClaimOut,
    ClaimPage,
    DecisionCreate,
    DecisionOut,
    DocumentOut,
    ExtractionOut,
    PipelineJobOut,
    RecommendationOut,
    ReviewSessionOut,
    TransitionRequest,
)
from app.states import TERMINAL, ClaimStatus, IllegalTransition, check_transition
from app.storage import Storage, get_storage

router = APIRouter(prefix="/claims", tags=["claims"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _move(session: SessionDep, claim: Claim, target: ClaimStatus, actor: str) -> None:
    previous = claim.status
    try:
        check_transition(ClaimStatus(previous), target, actor)
    except IllegalTransition as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    claim.status = target
    audit.record(
        session,
        actor=actor,
        event_type="claim.transitioned",
        claim_id=claim.id,
        payload={"from": previous, "to": target.value},
    )


@router.post("", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def create_claim(body: ClaimCreate, session: SessionDep, user: StaffUser) -> Claim:
    claim = Claim(**body.model_dump(), status=ClaimStatus.RECEIVED.value, created_by=user.id)
    session.add(claim)
    session.flush()
    audit.record(
        session,
        actor=actor_of(user),
        event_type="claim.created",
        claim_id=claim.id,
        payload={"claim_type": claim.claim_type},
    )
    session.commit()
    return claim


@router.get("", response_model=ClaimPage)
def list_claims(
    session: SessionDep,
    user: StaffUser,
    status_filter: Annotated[ClaimStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ClaimPage:
    query = select(Claim)
    count = select(func.count()).select_from(Claim)
    # A pilot tester sees only the claims assigned to them, never the other testers' copies.
    assigned = select(PilotAssignment.claim_id).where(PilotAssignment.user_id == user.id)
    if session.scalar(select(func.count()).select_from(assigned.subquery())):
        query = query.where(Claim.id.in_(assigned))
        count = count.where(Claim.id.in_(assigned))
    if status_filter is not None:
        query = query.where(Claim.status == status_filter.value)
        count = count.where(Claim.status == status_filter.value)
    items = session.scalars(
        query.order_by(Claim.created_at.desc(), Claim.id).limit(limit).offset(offset)
    ).all()
    return ClaimPage(
        items=[ClaimOut.model_validate(c) for c in items],
        total=session.scalar(count) or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> Claim:
    return get_claim_or_404(session, claim_id)


@router.post("/{claim_id}/transition", response_model=ClaimOut)
def transition_claim(
    claim_id: uuid.UUID, body: TransitionRequest, session: SessionDep, admin: AdminUser
) -> Claim:
    """Admin-only manual move between non-terminal states. Terminal states go through decisions."""
    claim = get_claim_or_404(session, claim_id)
    if body.target in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Terminal states require a decision")
    _move(session, claim, body.target, actor_of(admin))
    session.commit()
    return claim


@router.post(
    "/{claim_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
def upload_document(
    claim_id: uuid.UUID,
    file: UploadFile,
    session: SessionDep,
    user: StaffUser,
    storage: Annotated[Storage, Depends(get_storage)],
) -> Document:
    claim = get_claim_or_404(session, claim_id)
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Empty file")

    doc_id = uuid.uuid4()
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    key = f"claims/{claim.id}/{doc_id}/{filename}"
    storage.put(key, data, content_type)

    doc = Document(
        id=doc_id,
        claim_id=claim.id,
        filename=filename,
        content_type=content_type,
        s3_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        uploaded_by=user.id,
    )
    session.add(doc)
    session.flush()
    actor = actor_of(user)
    audit.record(
        session,
        actor=actor,
        event_type="document.uploaded",
        claim_id=claim.id,
        payload={"document_id": str(doc.id), "filename": filename, "sha256": doc.sha256},
    )
    session.commit()
    return doc


@router.get("/{claim_id}/documents", response_model=list[DocumentOut])
def list_documents(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> list[Document]:
    get_claim_or_404(session, claim_id)
    return list(
        session.scalars(
            select(Document).where(Document.claim_id == claim_id).order_by(Document.created_at)
        )
    )


@router.get("/{claim_id}/documents/{document_id}/content")
def download_document(
    claim_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    user: StaffUser,
    storage: Annotated[Storage, Depends(get_storage)],
) -> Response:
    """The original file, for viewing next to the recommendation."""
    get_claim_or_404(session, claim_id)
    doc = session.get(Document, document_id)
    if doc is None or doc.claim_id != claim_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return Response(
        content=storage.get(doc.s3_key),
        media_type=doc.content_type,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@router.post(
    "/{claim_id}/review-sessions", response_model=ReviewSessionOut, status_code=status.HTTP_200_OK
)
def start_review(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> ReviewSession:
    """Start (or resume) the caller's review of a claim.

    The first call fixes the start time used for time per claim and the mode: the one a pilot
    assignment dictates, otherwise AI-assisted. Later calls return the same session.
    """
    claim = get_claim_or_404(session, claim_id)
    if claim.status in {s.value for s in TERMINAL}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Claim is already decided")
    existing = session.scalar(
        select(ReviewSession).where(
            ReviewSession.claim_id == claim.id, ReviewSession.user_id == user.id
        )
    )
    if existing is not None:
        return existing
    review = ReviewSession(
        claim_id=claim.id,
        user_id=user.id,
        mode=assigned_mode(session, user, claim.id) or "assisted",
        release=settings.release,
    )
    session.add(review)
    session.flush()
    audit.record(
        session,
        actor=actor_of(user),
        event_type="review.started",
        claim_id=claim.id,
        payload={"session_id": str(review.id), "mode": review.mode, "release": review.release},
    )
    session.commit()
    session.refresh(review)
    return review


@router.get("/{claim_id}/decision", response_model=DecisionOut)
def get_decision(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> Decision:
    get_claim_or_404(session, claim_id)
    decision = session.scalar(select(Decision).where(Decision.claim_id == claim_id))
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No decision yet")
    return decision


@router.post(
    "/{claim_id}/decisions", response_model=DecisionOut, status_code=status.HTTP_201_CREATED
)
def create_decision(
    claim_id: uuid.UUID, body: DecisionCreate, session: SessionDep, user: StaffUser
) -> Decision:
    claim = get_claim_or_404(session, claim_id)
    recommendation = session.scalar(
        select(Recommendation).where(Recommendation.claim_id == claim.id)
    )
    review: ReviewSession | None = None
    if body.review_session_id is not None:
        review = session.get(ReviewSession, body.review_session_id)
        if review is None or review.claim_id != claim.id or review.user_id != user.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown review session")
    if assigned_mode(session, user, claim.id) == "manual" and review is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Open the claim through a review session first"
        )
    manual = review is not None and review.mode == "manual"
    # A manual-baseline reviewer never saw the recommendation, so there is nothing to override.
    overridden = (
        not manual
        and recommendation is not None
        and recommendation.outcome != _as_recommendation(body.outcome.value)
    )
    if overridden and body.reason_code is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "reason_code is required to override"
        )

    actor = actor_of(user)
    _move(session, claim, ClaimStatus(body.outcome.value), actor)
    decision = Decision(
        claim_id=claim.id,
        decided_by=user.id,
        outcome=body.outcome.value,
        recommendation_id=recommendation.id if recommendation else None,
        overridden=overridden,
        reason_code=body.reason_code.value if body.reason_code and not manual else None,
        note=body.note,
        review_session_id=review.id if review else None,
        review_mode=review.mode if review else None,
        review_seconds=(datetime.now(UTC) - review.started_at).total_seconds() if review else None,
        longest_idle_seconds=body.longest_idle_seconds,
    )
    session.add(decision)
    session.flush()
    audit.record(
        session,
        actor=actor,
        event_type="decision.overridden" if overridden else "decision.created",
        claim_id=claim.id,
        payload={
            "decision_id": str(decision.id),
            "outcome": decision.outcome,
            "reason_code": decision.reason_code,
            "confirmed_fields": body.confirmed_fields,
        },
    )
    session.commit()
    return decision


def _as_recommendation(outcome: str) -> str:
    return {"APPROVED": "approve", "DENIED": "deny", "ESCALATED": "escalate"}[outcome]


@router.get("/{claim_id}/audit", response_model=list[AuditEventOut])
def claim_audit(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> list[AuditEvent]:
    get_claim_or_404(session, claim_id)
    forbid_in_manual_mode(session, user, claim_id)
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.claim_id == claim_id).order_by(AuditEvent.id)
        )
    )


@router.post("/{claim_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
def analyze_claim_request(
    claim_id: uuid.UUID, session: SessionDep, user: StaffUser
) -> dict[str, str]:
    """Start background analysis once the adjuster has uploaded the claim's documents."""
    claim = get_claim_or_404(session, claim_id)
    if claim.status != ClaimStatus.RECEIVED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is {claim.status}, not RECEIVED")
    has_docs = session.scalar(
        select(func.count()).select_from(Document).where(Document.claim_id == claim.id)
    )
    if not has_docs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload documents first")
    if not jobs.enqueue(session, claim.id, "extract", actor=actor_of(user)):
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Analysis was already requested")
    session.commit()
    return {"status": "queued"}


@router.get("/{claim_id}/pipeline", response_model=list[PipelineJobOut])
def pipeline_status(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> list[PipelineJob]:
    get_claim_or_404(session, claim_id)
    return list(
        session.scalars(
            select(PipelineJob)
            .where(PipelineJob.claim_id == claim_id)
            .order_by(PipelineJob.created_at)
        )
    )


@router.get("/{claim_id}/extraction", response_model=ExtractionOut)
def get_extraction(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> Extraction:
    get_claim_or_404(session, claim_id)
    forbid_in_manual_mode(session, user, claim_id)
    extraction = session.scalar(select(Extraction).where(Extraction.claim_id == claim_id))
    if extraction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No extraction yet")
    return extraction


@router.get("/{claim_id}/recommendation", response_model=RecommendationOut)
def get_recommendation(
    claim_id: uuid.UUID, session: SessionDep, user: StaffUser
) -> RecommendationOut:
    """The recommendation with each cited clause quoted in full, for side-by-side review."""
    claim = get_claim_or_404(session, claim_id)
    forbid_in_manual_mode(session, user, claim_id)
    rec = session.scalar(select(Recommendation).where(Recommendation.claim_id == claim_id))
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recommendation yet")
    clauses: dict[str, PolicyClause] = {}
    if claim.policy_id is not None and rec.cited_clause_ids:
        clauses = {
            c.clause_id: c
            for c in session.scalars(
                select(PolicyClause).where(
                    PolicyClause.policy_id == claim.policy_id,
                    PolicyClause.clause_id.in_(rec.cited_clause_ids),
                )
            )
        }
    out = RecommendationOut.model_validate(rec)
    out.cited_clauses = [
        CitedClause(clause_id=i, section_path=clauses[i].section_path, text=clauses[i].text)
        for i in rec.cited_clause_ids
        if i in clauses
    ]
    return out
