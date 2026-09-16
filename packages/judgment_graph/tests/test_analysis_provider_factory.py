from __future__ import annotations

from collections.abc import Iterator

import pytest
from judgment_graph.input.factory import create_analysis_provider
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider
from judgment_graph.input.stub import StubAnalysisProvider
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event, insert
from sqlalchemy.engine import Engine


@pytest.fixture
def l1_engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata = MetaData()
    content_items = Table(
        "content_items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("title", String),
        Column("source", String),
        Column("language", String),
        Column("text", String),
        Column("exposure", Integer),
    )
    content_base_analysis = Table(
        "content_base_analysis",
        metadata,
        Column("content_id", Integer, primary_key=True),
        Column("summary", String),
        Column("key_points", JSON),
        Column("entities", JSON),
        Column("fields", JSON),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(content_items),
            {
                "id": 101,
                "title": "Agent runtime notes",
                "source": "blog",
                "language": "en",
                "text": "Runtime notes text",
                "exposure": 42,
            },
        )
        conn.execute(
            insert(content_base_analysis),
            {
                "content_id": 101,
                "summary": "How to operate agent runtimes.",
                "key_points": ["queue work", "bound retries"],
                "entities": ["LangGraph", "Arq"],
                "fields": {
                    "tags": ["agent-engineering", "runtime"],
                    "embedding": [0.1, "0.2", "bad"],
                },
            },
        )
    yield engine


def test_factory_defaults_to_stub_provider() -> None:
    provider = create_analysis_provider()
    assert isinstance(provider, StubAnalysisProvider)


def test_factory_creates_sqlalchemy_provider(l1_engine: Engine) -> None:
    provider = create_analysis_provider("sqlalchemy", engine=l1_engine)
    assert isinstance(provider, SqlAlchemyAnalysisProvider)


def test_factory_reads_environment_for_provider_kind(
    monkeypatch: pytest.MonkeyPatch,
    l1_engine: Engine,
) -> None:
    monkeypatch.setenv("L2_ANALYSIS_PROVIDER", "sqlalchemy")
    provider = create_analysis_provider(engine=l1_engine)
    assert isinstance(provider, SqlAlchemyAnalysisProvider)


def test_sqlalchemy_provider_reads_l1_rows(l1_engine: Engine) -> None:
    analysis = SqlAlchemyAnalysisProvider(l1_engine).get(101)

    assert analysis.content_id == 101
    assert analysis.title == "Agent runtime notes"
    assert analysis.summary == "How to operate agent runtimes."
    assert analysis.key_points == ["queue work", "bound retries"]
    assert analysis.entities == ["LangGraph", "Arq"]
    assert analysis.tags == ["agent-engineering", "runtime"]
    assert analysis.embedding == [0.1, 0.2]
    assert analysis.text == "Runtime notes text"
    assert analysis.exposure == 42


def test_sqlalchemy_provider_missing_row_raises_key_error(l1_engine: Engine) -> None:
    with pytest.raises(KeyError, match="L1 base_analysis not found"):
        SqlAlchemyAnalysisProvider(l1_engine).get(404)


def test_sqlalchemy_provider_missing_base_analysis_raises_key_error(l1_engine: Engine) -> None:
    metadata = MetaData()
    content_items = Table("content_items", metadata, autoload_with=l1_engine)
    with l1_engine.begin() as conn:
        conn.execute(
            insert(content_items),
            {
                "id": 102,
                "title": "Missing analysis",
                "source": "blog",
                "language": "en",
                "text": "No analysis yet",
                "exposure": 1,
            },
        )

    with pytest.raises(KeyError, match="L1 base_analysis not found"):
        SqlAlchemyAnalysisProvider(l1_engine).get(102)


def test_sqlalchemy_provider_only_executes_reads(l1_engine: Engine) -> None:
    statements: list[str] = []

    @event.listens_for(l1_engine, "before_cursor_execute")
    def record_statement(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement.lstrip().split(maxsplit=1)[0].upper())

    SqlAlchemyAnalysisProvider(l1_engine).get(101)

    assert statements
    assert set(statements) == {"SELECT", "PRAGMA"}
