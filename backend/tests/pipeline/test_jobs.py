import uuid

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, Claim, PipelineJob, User
from app.pipeline import jobs
from app.security import hash_password


@pytest.fixture(autouse=True)
def empty_queue(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM pipeline_job"))


@pytest.fixture
def claim_id(app_sessionmaker: sessionmaker[Session]) -> uuid.UUID:
    with app_sessionmaker() as s:
        user = User(
            email=f"u{uuid.uuid4().hex[:8]}@x.com", password_hash=hash_password("x"), role="admin"
        )
        s.add(user)
        s.flush()
        claim = Claim(created_by=user.id)
        s.add(claim)
        s.commit()
        return claim.id


def _events(sm: sessionmaker[Session], claim_id: uuid.UUID, kind: str) -> int:
    with sm() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.claim_id == claim_id, AuditEvent.event_type == kind)
            )
            or 0
        )


def _drain(sm: sessionmaker[Session], handlers: dict[str, jobs.Handler], **kw: int) -> None:
    while jobs.run_once(sm, handlers, **kw):
        pass


def test_enqueue_is_idempotent(
    app_sessionmaker: sessionmaker[Session], claim_id: uuid.UUID
) -> None:
    with app_sessionmaker() as s:
        assert jobs.enqueue(s, claim_id, "extract") is True
        assert jobs.enqueue(s, claim_id, "extract") is False
        s.commit()
    assert _events(app_sessionmaker, claim_id, "pipeline.enqueued") == 1
    with pytest.raises(ValueError), app_sessionmaker() as s:
        jobs.enqueue(s, claim_id, "bogus")


def test_chain_runs_each_stage_once(
    app_sessionmaker: sessionmaker[Session], claim_id: uuid.UUID
) -> None:
    ran: list[str] = []

    def make(stage: str, nxt: str | None) -> jobs.Handler:
        def handler(session: Session, job: PipelineJob) -> str | None:
            ran.append(stage)
            return nxt

        return handler

    handlers = {"extract": make("extract", "analyze"), "analyze": make("analyze", None)}
    with app_sessionmaker() as s:
        jobs.enqueue(s, claim_id, "extract")
        s.commit()
    _drain(app_sessionmaker, handlers)
    assert ran == ["extract", "analyze"]
    assert _events(app_sessionmaker, claim_id, "pipeline.stage_succeeded") == 2
    # Re-enqueueing a finished stage does nothing, so a stage can never double-run.
    with app_sessionmaker() as s:
        assert jobs.enqueue(s, claim_id, "extract") is False


def test_failure_retries_then_marks_failed_and_can_be_retried(
    app_sessionmaker: sessionmaker[Session], claim_id: uuid.UUID
) -> None:
    calls = {"n": 0}

    def flaky(session: Session, job: PipelineJob) -> str | None:
        calls["n"] += 1
        raise RuntimeError("boom")

    with app_sessionmaker() as s:
        jobs.enqueue(s, claim_id, "extract")
        s.commit()
    _drain(app_sessionmaker, {"extract": flaky}, max_attempts=3)
    assert calls["n"] == 3
    assert _events(app_sessionmaker, claim_id, "pipeline.stage_failed") == 3
    with app_sessionmaker() as s:
        job = s.scalar(select(PipelineJob).where(PipelineJob.claim_id == claim_id))
        assert job and job.status == "failed" and "boom" in (job.last_error or "")
        assert jobs.retry(s, claim_id, "extract", actor="user:test") is True
        s.commit()

    _drain(app_sessionmaker, {"extract": lambda s, j: None})
    with app_sessionmaker() as s:
        job = s.scalar(select(PipelineJob).where(PipelineJob.claim_id == claim_id))
        assert job and job.status == "succeeded"


def test_handler_side_effects_roll_back_on_failure(
    app_sessionmaker: sessionmaker[Session], claim_id: uuid.UUID
) -> None:
    def writes_then_fails(session: Session, job: PipelineJob) -> str | None:
        from app import audit

        audit.record(session, actor="x", event_type="should.not.persist", claim_id=job.claim_id)
        raise RuntimeError("late failure")

    with app_sessionmaker() as s:
        jobs.enqueue(s, claim_id, "extract")
        s.commit()
    _drain(app_sessionmaker, {"extract": writes_then_fails}, max_attempts=1)
    assert _events(app_sessionmaker, claim_id, "should.not.persist") == 0
