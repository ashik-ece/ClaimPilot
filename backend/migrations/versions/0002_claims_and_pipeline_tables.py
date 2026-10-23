"""claims, documents, extractions, recommendations, decisions

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_now = sa.text("now()")
_uuid = sa.text("gen_random_uuid()")
_STATES = (
    "'RECEIVED','EXTRACTED','ANALYZED','AWAITING_ADJUDICATION',"
    "'APPROVED','DENIED','ESCALATED','NEEDS_REVIEW'"
)


def _id() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=_uuid)


def _fk(name: str, table: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey(f"{table}.id"), nullable=nullable)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now, nullable=False)


def upgrade() -> None:
    op.create_table(
        "claim",
        _id(),
        _fk("policy_id", "policy", nullable=True),
        sa.Column("claimant_name", sa.String(255)),
        sa.Column("claim_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("status", sa.String(30), nullable=False),
        _fk("created_by", "user"),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now),
        sa.CheckConstraint(f"status in ({_STATES})", name="ck_claim_status"),
    )
    op.create_index("ix_claim_status_created", "claim", ["status", "created_at"])

    op.create_table(
        "document",
        _id(),
        _fk("claim_id", "claim"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("s3_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        _fk("uploaded_by", "user"),
        _created(),
    )
    op.create_index("ix_document_claim_id", "document", ["claim_id"])

    op.create_table(
        "extraction",
        _id(),
        _fk("claim_id", "claim"),
        _fk("document_id", "document"),
        sa.Column("fields", pg.JSONB, nullable=False),
        sa.Column("prompt_version", sa.String(50)),
        _created(),
        sa.UniqueConstraint("claim_id", "document_id"),
    )
    op.create_index("ix_extraction_claim_id", "extraction", ["claim_id"])

    op.create_table(
        "recommendation",
        _id(),
        _fk("claim_id", "claim"),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("cited_clause_ids", pg.JSONB, nullable=False),
        sa.Column("fraud_flags", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("model_name", sa.String(100)),
        sa.Column("prompt_version", sa.String(50)),
        _created(),
        sa.UniqueConstraint("claim_id"),
        sa.CheckConstraint("outcome in ('approve','deny','escalate')", name="ck_rec_outcome"),
    )

    op.create_table(
        "decision",
        _id(),
        _fk("claim_id", "claim"),
        _fk("decided_by", "user"),
        sa.Column("outcome", sa.String(20), nullable=False),
        _fk("recommendation_id", "recommendation", nullable=True),
        sa.Column("overridden", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reason_code", sa.String(50)),
        sa.Column("note", sa.Text),
        _created(),
        sa.UniqueConstraint("claim_id"),
        sa.CheckConstraint("outcome in ('APPROVED','DENIED','ESCALATED')", name="ck_dec_outcome"),
    )


def downgrade() -> None:
    for table in ("decision", "recommendation", "extraction", "document", "claim"):
        op.drop_table(table)
