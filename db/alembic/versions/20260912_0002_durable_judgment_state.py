"""Persist L2 lifecycle, cost counters and completion events.

Revision ID: 20260912_0002
Revises: 20260530_0001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260912_0002"
down_revision = "20260530_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_judgment_state",
        sa.Column("content_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("status", sa.String(16)),
        sa.Column("cost_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "judgment_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_type", "content_id", name="uq_judgment_outbox_content_event"),
    )


def downgrade() -> None:
    op.drop_table("judgment_outbox")
    op.drop_table("content_judgment_state")
