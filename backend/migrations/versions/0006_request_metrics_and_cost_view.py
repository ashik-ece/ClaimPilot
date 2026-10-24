"""request latency table and per-claim model cost view

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_metric",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Float, nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("claim_id", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_request_metric_created_at", "request_metric", ["created_at"])
    op.execute("GRANT SELECT, INSERT ON request_metric TO claimpilot_app")

    # Model spend per claim, summed from the token counts logged on each model.call event.
    # Replayed calls (tests, evaluation) are counted separately and carry no live cost.
    op.execute(
        """
        CREATE VIEW claim_model_cost AS
        SELECT claim_id,
               count(*) FILTER (WHERE NOT (payload->>'replayed')::boolean) AS live_calls,
               count(*) FILTER (WHERE (payload->>'replayed')::boolean) AS replayed_calls,
               coalesce(sum((payload->>'input_tokens')::bigint)
                        FILTER (WHERE NOT (payload->>'replayed')::boolean), 0) AS input_tokens,
               coalesce(sum((payload->>'output_tokens')::bigint)
                        FILTER (WHERE NOT (payload->>'replayed')::boolean), 0) AS output_tokens,
               coalesce(sum((payload->>'cost_usd')::numeric
                        ) FILTER (WHERE NOT (payload->>'replayed')::boolean), 0) AS cost_usd
        FROM audit_event
        WHERE event_type = 'model.call' AND claim_id IS NOT NULL
        GROUP BY claim_id
        """
    )
    op.execute("GRANT SELECT ON claim_model_cost TO claimpilot_app")


def downgrade() -> None:
    op.execute("DROP VIEW claim_model_cost")
    op.drop_table("request_metric")
