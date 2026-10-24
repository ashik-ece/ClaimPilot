"""claim-level extraction and recommendation details

An extraction now describes the whole claim (merged from all its documents), so document_id
becomes optional and a claim has one extraction. `meta` holds checks, issues and rule findings.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("extraction_claim_id_document_id_key", "extraction", type_="unique")
    op.alter_column("extraction", "document_id", nullable=True)
    op.create_unique_constraint("extraction_claim_id_key", "extraction", ["claim_id"])
    op.add_column("extraction", sa.Column("meta", pg.JSONB, nullable=False, server_default="{}"))
    op.add_column(
        "recommendation", sa.Column("meta", pg.JSONB, nullable=False, server_default="{}")
    )


def downgrade() -> None:
    op.drop_column("recommendation", "meta")
    op.drop_column("extraction", "meta")
    op.drop_constraint("extraction_claim_id_key", "extraction", type_="unique")
    op.alter_column("extraction", "document_id", nullable=False)
    op.create_unique_constraint(
        "extraction_claim_id_document_id_key", "extraction", ["claim_id", "document_id"]
    )
