from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine

from judgment_graph.input.provider import AnalysisProvider
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider, create_l1_engine
from judgment_graph.input.stub import StubAnalysisProvider


def create_analysis_provider(
    kind: str | None = None,
    *,
    fixture_path: Path | None = None,
    engine: Engine | None = None,
    database_url: str | None = None,
) -> AnalysisProvider:
    default_kind = "sqlalchemy" if os.getenv("L2_L1_DATABASE_URL") else "stub"
    provider_kind = (kind or os.getenv("L2_ANALYSIS_PROVIDER") or default_kind).lower()
    if provider_kind == "stub":
        return StubAnalysisProvider(fixture_path=fixture_path)
    if provider_kind == "sqlalchemy":
        if engine is None:
            resolved_url = database_url or os.getenv("L2_L1_DATABASE_URL")
            if not resolved_url:
                raise ValueError("sqlalchemy analysis provider requires L2_L1_DATABASE_URL")
            engine = create_l1_engine(resolved_url)
        return SqlAlchemyAnalysisProvider(engine)
    raise ValueError(f"unknown analysis provider kind: {provider_kind}")
