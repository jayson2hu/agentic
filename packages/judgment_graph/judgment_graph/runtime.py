from __future__ import annotations

import os

from judgment_graph.persist.contracts import JudgmentRepository
from judgment_graph.persist.repository import InMemoryJudgmentRepository
from judgment_graph.persist.sqlalchemy_repository import (
    SqlAlchemyJudgmentRepository,
    create_sqlalchemy_engine,
)


def create_judgment_repository(database_url: str | None = None) -> JudgmentRepository:
    resolved_url = database_url or os.getenv("L2_DATABASE_URL")
    if not resolved_url:
        return InMemoryJudgmentRepository()
    return SqlAlchemyJudgmentRepository(create_sqlalchemy_engine(resolved_url))
