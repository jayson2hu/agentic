"""Track the accepted L1 run and revision for versioned rescoring.

Revision ID: 20260916_0003
Revises: 20260912_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260916_0003"
down_revision = "20260912_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_judgment_state",
        sa.Column("source_run_id", sa.String(64)),
    )
    op.add_column(
        "content_judgment_state",
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "judgment_outbox",
        sa.Column("source_run_id", sa.String(64)),
    )
    op.add_column(
        "judgment_outbox",
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("judgment_outbox") as batch:
        batch.drop_constraint("uq_judgment_outbox_content_event", type_="unique")
        batch.create_unique_constraint(
            "uq_judgment_outbox_content_event_revision",
            ["event_type", "content_id", "source_revision"],
        )


def downgrade() -> None:
    with op.batch_alter_table("judgment_outbox") as batch:
        batch.drop_constraint(
            "uq_judgment_outbox_content_event_revision",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_judgment_outbox_content_event",
            ["event_type", "content_id"],
        )
    op.drop_column("judgment_outbox", "source_revision")
    op.drop_column("judgment_outbox", "source_run_id")
    op.drop_column("content_judgment_state", "source_revision")
    op.drop_column("content_judgment_state", "source_run_id")
