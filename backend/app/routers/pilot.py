"""Pilot support: a tester's assigned tasks and the feedback form."""

from fastapi import APIRouter, status
from sqlalchemy import select

from app.config import settings
from app.deps import SessionDep, StaffUser
from app.models import Claim, Decision, PilotAssignment, PilotFeedback
from app.schemas import FeedbackCreate, PilotTaskOut
from app.states import TERMINAL

router = APIRouter(prefix="/pilot", tags=["pilot"])


@router.get("/tasks", response_model=list[PilotTaskOut])
def my_tasks(session: SessionDep, user: StaffUser) -> list[PilotTaskOut]:
    """The caller's assigned claims in the order they should be worked."""
    rows = session.execute(
        select(PilotAssignment, Claim.status)
        .join(Claim, Claim.id == PilotAssignment.claim_id)
        .where(PilotAssignment.user_id == user.id)
        .order_by(PilotAssignment.round, PilotAssignment.position)
    ).all()
    decided = set(session.scalars(select(Decision.claim_id).where(Decision.decided_by == user.id)))
    return [
        PilotTaskOut(
            claim_id=a.claim_id,
            position=a.position,
            round=a.round,
            mode=a.mode,
            status=claim_status,
            done=a.claim_id in decided or claim_status in {s.value for s in TERMINAL},
        )
        for a, claim_status in rows
    ]


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def submit_feedback(body: FeedbackCreate, session: SessionDep, user: StaffUser) -> dict[str, str]:
    session.add(PilotFeedback(user_id=user.id, release=settings.release, **body.model_dump()))
    session.commit()
    return {"status": "recorded"}
