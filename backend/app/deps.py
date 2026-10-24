import uuid
from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Claim, PilotAssignment, User
from app.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

SessionDep = Annotated[Session, Depends(get_session)]


def current_user(token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise unauthorized from None
    user = session.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user

    return checker


AdminUser = Annotated[User, Depends(require_role("admin"))]
StaffUser = Annotated[User, Depends(require_role("adjuster", "admin"))]


def actor_of(user: User) -> str:
    return f"user:{user.id}"


def get_claim_or_404(session: Session, claim_id: uuid.UUID) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    return claim


def assigned_mode(session: Session, user: User, claim_id: uuid.UUID) -> str | None:
    """The review mode a pilot tester must use for this claim, or None outside the pilot."""
    return session.scalar(
        select(PilotAssignment.mode).where(
            PilotAssignment.user_id == user.id, PilotAssignment.claim_id == claim_id
        )
    )


def forbid_in_manual_mode(session: Session, user: User, claim_id: uuid.UUID) -> None:
    """Manual-baseline testers must not see AI output, whatever client they use."""
    if assigned_mode(session, user, claim_id) == "manual":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available in manual mode")
