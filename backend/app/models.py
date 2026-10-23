import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid_pk() -> Any:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _now()


class Policy(Base):
    __tablename__ = "policy"
    __table_args__ = (UniqueConstraint("policy_number", "version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    policy_number: Mapped[str] = mapped_column(String(50))
    version: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    effective_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _now()


class PolicyClause(Base):
    __tablename__ = "policy_clause"
    __table_args__ = (UniqueConstraint("policy_id", "clause_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy.id"))
    clause_id: Mapped[str] = mapped_column(String(50))
    section_path: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)


class Claim(Base):
    __tablename__ = "claim"
    __table_args__ = (Index("ix_claim_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy.id"))
    claimant_name: Mapped[str | None] = mapped_column(String(255))
    claim_type: Mapped[str] = mapped_column(String(50), default="auto_collision")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Document(Base):
    __tablename__ = "document"

    id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claim.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    s3_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = _now()


class Extraction(Base):
    __tablename__ = "extraction"
    __table_args__ = (UniqueConstraint("claim_id", "document_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claim.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"))
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = _now()


class Recommendation(Base):
    __tablename__ = "recommendation"
    __table_args__ = (UniqueConstraint("claim_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claim.id"))
    outcome: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str] = mapped_column(Text)
    cited_clause_ids: Mapped[list[str]] = mapped_column(JSONB)
    fraud_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    model_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = _now()


class Decision(Base):
    __tablename__ = "decision"
    __table_args__ = (UniqueConstraint("claim_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claim.id"))
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    outcome: Mapped[str] = mapped_column(String(20))
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recommendation.id"))
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_code: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claim.id"), index=True, nullable=True
    )
    actor: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model_name: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


class PipelineJob(Base):
    __tablename__ = "pipeline_job"
    __table_args__ = (
        UniqueConstraint("claim_id", "stage"),
        Index("ix_pipeline_job_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claim.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
