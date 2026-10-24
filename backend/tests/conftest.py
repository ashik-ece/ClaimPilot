import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import User
from app.security import hash_password
from app.storage import get_storage


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.fixture(scope="session")
def test_db_urls() -> Iterator[tuple[str, str]]:
    """Create a throwaway database; yield (owner_url, app_role_url)."""
    owner = make_url(settings.migration_database_url)
    app_role = make_url(settings.database_url)
    name = f"claimpilot_test_{uuid.uuid4().hex[:8]}"

    admin = create_engine(owner.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    owner_url = owner.set(database=name).render_as_string(hide_password=False)
    app_url = app_role.set(database=name).render_as_string(hide_password=False)

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "migrations")
    )
    cfg.attributes["url"] = owner_url
    command.upgrade(cfg, "head")

    yield owner_url, app_url

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def app_sessionmaker(test_db_urls: tuple[str, str]) -> sessionmaker[Session]:
    return sessionmaker(bind=create_engine(test_db_urls[1]), expire_on_commit=False)


@pytest.fixture(scope="session")
def owner_engine(test_db_urls: tuple[str, str]) -> Any:
    return create_engine(test_db_urls[0])


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(app_sessionmaker: sessionmaker[Session], storage: FakeStorage) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        with app_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_user(sm: sessionmaker[Session], role: str) -> tuple[str, str]:
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    with sm() as session:
        session.add(User(email=email, password_hash=hash_password("password123"), role=role))
        session.commit()
    return email, "password123"


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def adjuster_headers(client: TestClient, app_sessionmaker: sessionmaker[Session]) -> dict[str, str]:
    return _login(client, *_make_user(app_sessionmaker, "adjuster"))


@pytest.fixture
def admin_headers(client: TestClient, app_sessionmaker: sessionmaker[Session]) -> dict[str, str]:
    return _login(client, *_make_user(app_sessionmaker, "admin"))
