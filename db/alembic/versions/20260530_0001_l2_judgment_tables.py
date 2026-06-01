"""create l2 judgment tables

Revision ID: 20260530_0001
Revises:
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260530_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "verticals",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "source_filter",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rubric_prompt", sa.Text(), nullable=False),
        sa.Column("rubric_version", sa.Text(), nullable=False),
        sa.Column(
            "tag_taxonomy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("brief_template", sa.Text(), nullable=False),
        sa.Column(
            "model_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "content_vertical_scores",
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("vertical_code", sa.Text(), sa.ForeignKey("verticals.code"), nullable=False),
        sa.Column("relevance", sa.Integer(), nullable=False),
        sa.Column("dim_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "vertical_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("review_note", sa.Text()),
        sa.Column("rubric_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("content_id", "vertical_code"),
    )
    op.create_table(
        "content_translations",
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("content_id", "lang"),
    )
    op.create_table(
        "review_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("vertical_code", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reviewer", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("review_queue")
    op.drop_table("content_translations")
    op.drop_table("content_vertical_scores")
    op.drop_table("verticals")
