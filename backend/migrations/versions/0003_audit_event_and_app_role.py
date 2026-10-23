"""append-only audit_event and restricted application role

The application role gets INSERT and SELECT on audit_event and nothing else, so the
append-only guarantee is enforced by the database rather than by application code.
Future migrations that add tables must grant privileges to claimpilot_app explicitly.

Revision ID: 0003
Revises: 0002
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "claimpilot_app"
MUTABLE_TABLES = (
    "user",
    "policy",
    "policy_clause",
    "claim",
    "document",
    "extraction",
    "recommendation",
    "decision",
)


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("claim_id", pg.UUID(as_uuid=True), sa.ForeignKey("claim.id"), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("model_name", sa.String(100)),
        sa.Column("model_version", sa.String(50)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_event_claim_id", "audit_event", ["claim_id"])

    password = os.environ.get("APP_DB_PASSWORD", "claimpilot_app").replace("'", "''")
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{password}';
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    for table in MUTABLE_TABLES:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE}')
    op.execute(f"REVOKE ALL ON audit_event FROM PUBLIC, {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_event TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("audit_event")
