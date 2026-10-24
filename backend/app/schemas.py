import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.states import ClaimStatus


class Role(StrEnum):
    adjuster = "adjuster"
    admin = "admin"


class ReasonCode(StrEnum):
    """Fixed list so override analysis is quantitative."""

    misread_document = "misread_document"
    wrong_clause = "wrong_clause"
    missed_fraud_signal = "missed_fraud_signal"
    false_fraud_flag = "false_fraud_flag"
    coverage_interpretation = "coverage_interpretation"
    other = "other"


class Outcome(StrEnum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    ESCALATED = "ESCALATED"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: Role = Role.adjuster


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    role: str


class ClaimCreate(BaseModel):
    claimant_name: str | None = None
    claim_type: str = "auto_collision"
    amount: Decimal | None = Field(default=None, ge=0)
    policy_id: uuid.UUID | None = None


class ClaimOut(ORMModel):
    id: uuid.UUID
    policy_id: uuid.UUID | None
    claimant_name: str | None
    claim_type: str
    amount: Decimal | None
    status: ClaimStatus
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ClaimPage(BaseModel):
    items: list[ClaimOut]
    total: int
    limit: int
    offset: int


class TransitionRequest(BaseModel):
    target: ClaimStatus


class DocumentOut(ORMModel):
    id: uuid.UUID
    claim_id: uuid.UUID
    filename: str
    content_type: str
    s3_key: str
    sha256: str
    size_bytes: int
    created_at: datetime


class PolicyCreate(BaseModel):
    policy_number: str
    version: str
    name: str
    effective_date: date
    expiry_date: date


class PolicyOut(ORMModel):
    id: uuid.UUID
    policy_number: str
    version: str
    name: str
    effective_date: date
    expiry_date: date


class ClauseCreate(BaseModel):
    clause_id: str = Field(min_length=1, max_length=50)
    section_path: str = Field(max_length=255)
    text: str = Field(min_length=1)


class ClauseOut(ORMModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    clause_id: str
    section_path: str
    text: str


class DecisionCreate(BaseModel):
    outcome: Outcome
    reason_code: ReasonCode | None = None
    note: str | None = None


class DecisionOut(ORMModel):
    id: uuid.UUID
    claim_id: uuid.UUID
    decided_by: uuid.UUID
    outcome: str
    recommendation_id: uuid.UUID | None
    overridden: bool
    reason_code: str | None
    note: str | None
    created_at: datetime


class AuditEventOut(ORMModel):
    id: int
    claim_id: uuid.UUID | None
    actor: str
    event_type: str
    payload: dict[str, Any]
    model_name: str | None
    model_version: str | None
    prompt_version: str | None
    created_at: datetime


class ExtractionOut(ORMModel):
    claim_id: uuid.UUID
    fields: dict[str, Any]
    meta: dict[str, Any]
    prompt_version: str | None
    created_at: datetime


class CitedClause(BaseModel):
    clause_id: str
    section_path: str
    text: str


class RecommendationOut(ORMModel):
    id: uuid.UUID
    claim_id: uuid.UUID
    outcome: str
    rationale: str
    cited_clause_ids: list[str]
    cited_clauses: list[CitedClause] = []
    fraud_flags: list[dict[str, Any]]
    meta: dict[str, Any]
    model_name: str | None
    prompt_version: str | None
    created_at: datetime


class PipelineJobOut(ORMModel):
    stage: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None
