from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

metadata = MetaData()


def json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


verticals = Table(
    "verticals",
    metadata,
    # Lens configuration is owned by L2 and injected into all judgment graphs.
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("source_filter", json_type(), nullable=False, default=dict),
    Column("rubric_prompt", Text, nullable=False),
    Column("rubric_version", Text, nullable=False),
    Column("tag_taxonomy", json_type(), nullable=False, default=dict),
    Column("brief_template", Text, nullable=False),
    Column("model_profile", json_type(), nullable=False, default=dict),
)

content_vertical_scores = Table(
    "content_vertical_scores",
    metadata,
    Column("content_id", BigInteger, primary_key=True),
    Column("vertical_code", Text, primary_key=True),
    Column("relevance", Integer, nullable=False),
    Column("dim_scores", json_type(), nullable=False),
    Column("vertical_tags", json_type(), nullable=False, default=list),
    Column("quality_score", Integer, nullable=False),
    Column("reviewed", Boolean, nullable=False, default=False),
    Column("review_note", Text),
    Column("rubric_version", Text, nullable=False),
    Column("model", Text, nullable=False),
)

content_translations = Table(
    "content_translations",
    metadata,
    Column("content_id", BigInteger, primary_key=True),
    Column("lang", String(8), primary_key=True),
    Column("fields", json_type(), nullable=False),
    Column("model", Text, nullable=False),
)

review_queue = Table(
    "review_queue",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", BigInteger, nullable=False),
    Column("vertical_code", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("status", String(16), nullable=False, default="pending"),
    Column("reviewer", Text),
    Column("decided_at", DateTime(timezone=True)),
)


content_judgment_state = Table(
    "content_judgment_state",
    metadata,
    Column("content_id", BigInteger, primary_key=True, autoincrement=False),
    Column("status", String(16)),
    Column("cost_units", Integer, nullable=False, default=0, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)),
)

judgment_outbox = Table(
    "judgment_outbox",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", String(64), nullable=False),
    Column("content_id", BigInteger, nullable=False),
    Column("payload", json_type(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)),
    UniqueConstraint("event_type", "content_id", name="uq_judgment_outbox_content_event"),
)
