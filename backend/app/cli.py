"""Bootstrap helper: python -m app.cli create-user EMAIL PASSWORD [adjuster|admin]"""

import sys

from sqlalchemy import select

from app import audit
from app.db import SessionLocal
from app.models import User
from app.security import hash_password


def create_user(email: str, password: str, role: str = "admin") -> None:
    with SessionLocal() as session:
        if session.scalar(select(User).where(User.email == email)):
            print(f"{email} already exists")
            return
        user = User(email=email, password_hash=hash_password(password), role=role)
        session.add(user)
        session.flush()
        audit.record(
            session,
            actor="system:cli",
            event_type="user.created",
            payload={"user_id": str(user.id), "role": role},
        )
        session.commit()
        print(f"created {role} {email}")


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "create-user":
        sys.exit(__doc__)
    create_user(sys.argv[2], sys.argv[3], *sys.argv[4:5])
