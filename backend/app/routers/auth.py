from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app import audit
from app.deps import AdminUser, CurrentUser, SessionDep, actor_of
from app.models import User
from app.schemas import TokenResponse, UserCreate, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep
) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    audit.record(session, actor=actor_of(user), event_type="user.login")
    session.commit()
    return TokenResponse(access_token=create_token(str(user.id), user.role))


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, session: SessionDep, admin: AdminUser) -> User:
    if session.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=body.email, password_hash=hash_password(body.password), role=body.role)
    session.add(user)
    session.flush()
    audit.record(
        session,
        actor=actor_of(admin),
        event_type="user.created",
        payload={"user_id": str(user.id), "role": user.role},
    )
    session.commit()
    return user
