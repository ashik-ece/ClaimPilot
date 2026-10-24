"""pipeline_job queue table

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_job",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("claim_id", pg.UUID(as_uuid=True), sa.ForeignKey("claim.id"), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("claim_id", "stage"),
    )
    op.create_index("ix_pipeline_job_claim_id", "pipeline_job", ["claim_id"])
    op.create_index("ix_pipeline_job_status_created", "pipeline_job", ["status", "created_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_job TO claimpilot_app")


def downgrade() -> None:
    op.drop_table("pipeline_job")
