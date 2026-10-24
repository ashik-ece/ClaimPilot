import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    session: Session,
    *,
    actor: str,
    event_type: str,
    claim_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> AuditEvent:
    """Append an audit event in the caller's transaction (commits with the mutation)."""
    event = AuditEvent(
        claim_id=claim_id,
        actor=actor,
        event_type=event_type,
        payload=payload or {},
        model_name=model_name,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    session.add(event)
    session.flush()
    return event
