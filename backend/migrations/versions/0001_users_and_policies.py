"""users and policies

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_now = sa.text("now()")
_uuid = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=_uuid),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now, nullable=False),
        sa.CheckConstraint("role in ('adjuster', 'admin')", name="ck_user_role"),
    )
    op.create_table(
        "policy",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=_uuid),
        sa.Column("policy_number", sa.String(50), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now, nullable=False),
        sa.UniqueConstraint("policy_number", "version"),
    )
    op.create_table(
        "policy_clause",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=_uuid),
        sa.Column("policy_id", pg.UUID(as_uuid=True), sa.ForeignKey("policy.id"), nullable=False),
        sa.Column("clause_id", sa.String(50), nullable=False),
        sa.Column("section_path", sa.String(255), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.UniqueConstraint("policy_id", "clause_id"),
    )


def downgrade() -> None:
    op.drop_table("policy_clause")
    op.drop_table("policy")
    op.drop_table("user")
