import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app import audit


def _seed_event(sm: sessionmaker[Session]) -> int:
    with sm() as session:
        event = audit.record(session, actor="system:test", event_type="test.event")
        session.commit()
        return event.id


def test_app_role_can_insert_and_select(app_sessionmaker: sessionmaker[Session]) -> None:
    event_id = _seed_event(app_sessionmaker)
    with app_sessionmaker() as session:
        assert session.execute(
            text("SELECT 1 FROM audit_event WHERE id = :i"), {"i": event_id}
        ).scalar()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_event SET actor = 'tampered' WHERE id = :i",
        "DELETE FROM audit_event WHERE id = :i",
        "TRUNCATE audit_event",
    ],
)
def test_app_role_cannot_modify_audit_events(
    app_sessionmaker: sessionmaker[Session], statement: str
) -> None:
    event_id = _seed_event(app_sessionmaker)
    with (
        app_sessionmaker() as session,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        session.execute(text(statement), {"i": event_id})
