import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app import audit
from app.deps import AdminUser, SessionDep, StaffUser, actor_of
from app.models import Policy, PolicyClause
from app.schemas import ClauseCreate, ClauseOut, PolicyCreate, PolicyOut

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


@router.get("/{policy_id}/clauses", response_model=list[ClauseOut])
def list_clauses(policy_id: uuid.UUID, session: SessionDep, user: StaffUser) -> list[PolicyClause]:
    get_policy(policy_id, session, user)
    return list(
        session.scalars(
            select(PolicyClause)
            .where(PolicyClause.policy_id == policy_id)
            .order_by(PolicyClause.clause_id)
        )
    )


@router.post(
    "/{policy_id}/clauses", response_model=list[ClauseOut], status_code=status.HTTP_201_CREATED
)
def add_clauses(
    policy_id: uuid.UUID, body: list[ClauseCreate], session: SessionDep, admin: AdminUser
) -> list[PolicyClause]:
    """Bulk-add clauses to a policy version. Clause IDs must be unique within the policy."""
    get_policy(policy_id, session, admin)
    ids = [c.clause_id for c in body]
    existing = set(
        session.scalars(
            select(PolicyClause.clause_id).where(
                PolicyClause.policy_id == policy_id, PolicyClause.clause_id.in_(ids)
            )
        )
    )
    if existing or len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_409_CONFLICT, "Duplicate clause_id")
    clauses = [PolicyClause(policy_id=policy_id, **c.model_dump()) for c in body]
    session.add_all(clauses)
    session.flush()
    audit.record(
        session,
        actor=actor_of(admin),
        event_type="policy.clauses_added",
        payload={"policy_id": str(policy_id), "count": len(clauses)},
    )
    session.commit()
    return clauses
