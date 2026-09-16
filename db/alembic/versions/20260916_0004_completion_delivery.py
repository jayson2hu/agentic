"""Track reliable delivery of L2 completion events.

Revision ID: 20260916_0004
Revises: 20260916_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20260916_0004"
down_revision = "20260916_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("judgment_outbox", sa.Column("event_id", sa.String(180)))
    op.add_column(
        "judgment_outbox",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("judgment_outbox", sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.add_column("judgment_outbox", sa.Column("acked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "judgment_outbox",
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True)),
    )
    op.add_column("judgment_outbox", sa.Column("last_error", sa.Text()))
    op.execute(
        sa.text(
            "UPDATE judgment_outbox SET event_id = "
            "event_type || ':' || CAST(content_id AS VARCHAR) || "
            "'-r' || CAST(source_revision AS VARCHAR)"
        )
    )
    with op.batch_alter_table("judgment_outbox") as batch:
        batch.alter_column("event_id", existing_type=sa.String(180), nullable=False)
        batch.create_unique_constraint(
            "uq_judgment_outbox_event_id", ["event_id"]
        )
    op.create_index(
        "ix_judgment_outbox_delivery",
        "judgment_outbox",
        ["acked_at", "dead_lettered_at", "sent_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_judgment_outbox_delivery", table_name="judgment_outbox")
    with op.batch_alter_table("judgment_outbox") as batch:
        batch.drop_constraint("uq_judgment_outbox_event_id", type_="unique")
    op.drop_column("judgment_outbox", "last_error")
    op.drop_column("judgment_outbox", "dead_lettered_at")
    op.drop_column("judgment_outbox", "acked_at")
    op.drop_column("judgment_outbox", "sent_at")
    op.drop_column("judgment_outbox", "attempt_count")
    op.drop_column("judgment_outbox", "event_id")
