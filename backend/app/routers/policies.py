import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app import audit
from app.deps import AdminUser, SessionDep, StaffUser, actor_of
from app.models import Policy
from app.schemas import PolicyCreate, PolicyOut

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyOut])
def list_policies(session: SessionDep, user: StaffUser) -> list[Policy]:
    return list(session.scalars(select(Policy).order_by(Policy.policy_number, Policy.version)))


@router.get("/{policy_id}", response_model=PolicyOut)
def get_policy(policy_id: uuid.UUID, session: SessionDep, user: StaffUser) -> Policy:
    policy = session.get(Policy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    return policy


@router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
def create_policy(body: PolicyCreate, session: SessionDep, admin: AdminUser) -> Policy:
    if session.scalar(
        select(Policy).where(
            Policy.policy_number == body.policy_number, Policy.version == body.version
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Policy version already exists")
    policy = Policy(**body.model_dump())
    session.add(policy)
    session.flush()
    audit.record(
        session,
        actor=actor_of(admin),
        event_type="policy.created",
        payload={"policy_id": str(policy.id), "policy_number": policy.policy_number},
    )
    session.commit()
    return policy
