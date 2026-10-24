"""pilot support: review sessions, task assignments, feedback, decision timing

A review session starts when an adjuster first opens a claim for review; its server-side start
time is the clock for "time per claim". Assignments say which mode (AI-assisted or manual) a
pilot tester must use for a claim.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "review_session",
        _uuid_pk(),
        sa.Column("claim_id", pg.UUID(as_uuid=True), sa.ForeignKey("claim.id"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("release", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("mode IN ('assisted', 'manual')", name="ck_review_session_mode"),
        sa.UniqueConstraint("claim_id", "user_id"),
    )
    op.create_table(
        "pilot_assignment",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("claim_id", pg.UUID(as_uuid=True), sa.ForeignKey("claim.id"), nullable=False),
        sa.Column("tester", sa.String(20), nullable=False),
        sa.Column("source_claim", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("release", sa.String(20), nullable=False),
        sa.CheckConstraint("mode IN ('assisted', 'manual')", name="ck_pilot_assignment_mode"),
        sa.UniqueConstraint("user_id", "claim_id"),
    )
    op.create_table(
        "pilot_feedback",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("release", sa.String(20), nullable=False),
        sa.Column("usefulness", sa.SmallInteger, nullable=False),
        sa.Column("trust", sa.SmallInteger, nullable=False),
        sa.Column("top_complaint", sa.Text),
        sa.Column("top_missing", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("usefulness BETWEEN 1 AND 5 AND trust BETWEEN 1 AND 5"),
    )
    op.add_column("decision", sa.Column("review_session_id", pg.UUID(as_uuid=True)))
    op.add_column("decision", sa.Column("review_mode", sa.String(10)))
    op.add_column("decision", sa.Column("review_seconds", sa.Float))
    op.add_column("decision", sa.Column("longest_idle_seconds", sa.Integer))
    op.create_foreign_key(
        "fk_decision_review_session", "decision", "review_session", ["review_session_id"], ["id"]
    )
    for table in ("review_session", "pilot_assignment", "pilot_feedback"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO claimpilot_app")


def downgrade() -> None:
    op.drop_constraint("fk_decision_review_session", "decision", type_="foreignkey")
    for column in ("longest_idle_seconds", "review_seconds", "review_mode", "review_session_id"):
        op.drop_column("decision", column)
    op.drop_table("pilot_feedback")
    op.drop_table("pilot_assignment")
    op.drop_table("review_session")
