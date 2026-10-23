import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app import audit
from app.deps import AdminUser, SessionDep, StaffUser, actor_of, get_claim_or_404
from app.models import AuditEvent, Claim, Decision, Document, Recommendation
from app.schemas import (
    AuditEventOut,
    ClaimCreate,
    ClaimOut,
    ClaimPage,
    DecisionCreate,
    DecisionOut,
    DocumentOut,
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
    # Phase 3 replaces this marker with a real queue; the audit entry keeps the trail honest.
    audit.record(
        session,
        actor=actor,
        event_type="pipeline.enqueue_requested",
        claim_id=claim.id,
        payload={"stage": "extraction"},
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
    # A recommendation of approve/deny/escalate maps onto APPROVED/DENIED/ESCALATED.
    overridden = recommendation is not None and recommendation.outcome != _as_recommendation(
        body.outcome.value
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
        reason_code=body.reason_code.value if body.reason_code else None,
        note=body.note,
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
        },
    )
    session.commit()
    return decision


def _as_recommendation(outcome: str) -> str:
    return {"APPROVED": "approve", "DENIED": "deny", "ESCALATED": "escalate"}[outcome]


@router.get("/{claim_id}/audit", response_model=list[AuditEventOut])
def claim_audit(claim_id: uuid.UUID, session: SessionDep, user: StaffUser) -> list[AuditEvent]:
    get_claim_or_404(session, claim_id)
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.claim_id == claim_id).order_by(AuditEvent.id)
        )
    )
