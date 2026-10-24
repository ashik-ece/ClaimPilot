"""Postgres-backed job queue for the analysis pipeline.

One row per (claim, stage). Enqueueing is idempotent, so retries and double-clicks cannot
run a stage twice. Workers claim rows with `FOR UPDATE SKIP LOCKED`, so several workers can
run safely. A stage handler returns the next stage to enqueue, or None when the chain ends.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import audit
from app.models import PipelineJob

STAGES = ("extract", "analyze")
QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"

Handler = Callable[[Session, PipelineJob], str | None]
# Called inside the failure transaction when a stage has used its last attempt.
ExhaustedHook = Callable[[Session, uuid.UUID, str, str], None]


def enqueue(
    session: Session, claim_id: uuid.UUID, stage: str, *, actor: str = "system:pipeline"
) -> bool:
    """Queue a stage. Returns False (and writes nothing) if it was already queued or has run."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    stmt = (
        insert(PipelineJob)
        .values(claim_id=claim_id, stage=stage, status=QUEUED)
        .on_conflict_do_nothing(index_elements=["claim_id", "stage"])
        .returning(PipelineJob.id)
    )
    created = session.execute(stmt).scalar_one_or_none() is not None
    if created:
        audit.record(
            session,
            actor=actor,
            event_type="pipeline.enqueued",
            claim_id=claim_id,
            payload={"stage": stage},
        )
    return created


def retry(session: Session, claim_id: uuid.UUID, stage: str, *, actor: str) -> bool:
    """Re-queue a failed stage so an operator can retry it."""
    job = session.scalar(
        select(PipelineJob)
        .where(PipelineJob.claim_id == claim_id, PipelineJob.stage == stage)
        .with_for_update()
    )
    if job is None or job.status != FAILED:
        return False
    job.status, job.attempts, job.last_error = QUEUED, 0, None
    audit.record(
        session,
        actor=actor,
        event_type="pipeline.retry_requested",
        claim_id=claim_id,
        payload={"stage": stage},
    )
    return True


def claim_next(session: Session) -> PipelineJob | None:
    job = session.scalar(
        select(PipelineJob)
        .where(PipelineJob.status == QUEUED)
        .order_by(PipelineJob.created_at, PipelineJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = RUNNING
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    session.flush()
    return job


def run_once(
    session_factory: Callable[[], Session],
    handlers: dict[str, Handler],
    *,
    max_attempts: int = 3,
    on_exhausted: ExhaustedHook | None = None,
) -> bool:
    """Run one queued job. Returns False when the queue is empty.

    The handler runs in the same transaction as the status change, so a crash rolls both
    back and the job simply stays queued. A failing handler is rolled back, then the failure
    is recorded in a fresh transaction.
    """
    with session_factory() as session:
        job = claim_next(session)
        if job is None:
            return False
        job_id, claim_id, stage = job.id, job.claim_id, job.stage
        try:
            next_stage = handlers[stage](session, job)
            job.status, job.finished_at, job.last_error = SUCCEEDED, datetime.now(UTC), None
            audit.record(
                session,
                actor="system:pipeline",
                event_type="pipeline.stage_succeeded",
                claim_id=claim_id,
                payload={"stage": stage, "attempt": job.attempts},
            )
            if next_stage:
                enqueue(session, claim_id, next_stage)
            session.commit()
            return True
        except Exception as exc:  # noqa: BLE001 - any handler failure is recorded and retried
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:2000]
        _record_failure(session, job_id, claim_id, stage, error, max_attempts, on_exhausted)
        return True


def _record_failure(
    session: Session,
    job_id: uuid.UUID,
    claim_id: uuid.UUID,
    stage: str,
    error: str,
    limit: int,
    on_exhausted: ExhaustedHook | None,
) -> None:
    job = session.get(PipelineJob, job_id, with_for_update=True)
    assert job is not None
    # claim_next's attempt bump was rolled back with the handler, so count it again here.
    job.attempts += 1
    exhausted = job.attempts >= limit
    job.status = FAILED if exhausted else QUEUED
    job.last_error = error
    payload: dict[str, Any] = {
        "stage": stage,
        "attempt": job.attempts,
        "error": error,
        "will_retry": not exhausted,
    }
    audit.record(
        session,
        actor="system:pipeline",
        event_type="pipeline.stage_failed",
        claim_id=claim_id,
        payload=payload,
    )
    if exhausted and on_exhausted is not None:
        on_exhausted(session, claim_id, stage, error)
    session.commit()
