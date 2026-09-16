from copy import deepcopy
from datetime import UTC, datetime

import pytest
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    insert,
    update,
)


def snapshot_row() -> dict:
    return {
        "schema_version": 1, "content_id": "101", "graph_version": "l1-v1",
        "content_hash": "body-hash", "run_id": "run-101", "status": "WAIT_SCORE",
        "updated_at": datetime.now(UTC),
        "input_snapshot": {
            "content_id": "101", "title": "AI coding agents", "body": "Agent workflow details.",
            "source_url": "https://example.test/agents", "author": None, "published_at": None,
            "metadata": {"source": {"id": 8, "name": "Engineering Blog"}, "lang": "zh", "exposure": 42},
        },
        "analysis": {
            "content_id": "101", "one_liner": "AI workflows", "summary": "How to test AI agents.",
            "key_points": ["Bound retries"], "quotes": [], "entities": ["Arq"],
            "base_tags": ["agent-engineering"], "embedding": [0.1, 1, -0.3],
            "lang": "en", "status": "COMPLETED", "created_at": "2026-09-12T00:00:00Z", "traces": [],
        },
    }


@pytest.fixture
def snapshot_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    table = Table(
        "content_base_analysis", metadata,
        Column("schema_version", Integer), Column("content_id", String, primary_key=True),
        Column("input_snapshot", JSON), Column("analysis", JSON), Column("graph_version", String),
        Column("content_hash", String), Column("run_id", String), Column("status", String),
        Column("updated_at", DateTime(timezone=True)),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(table).values(**snapshot_row()))
    yield engine, table
    engine.dispose()


def test_snapshot_contract_needs_no_l0_table_and_preserves_l1_fields(snapshot_database):
    engine, _table = snapshot_database
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())

    provider = SqlAlchemyAnalysisProvider(engine)
    result = provider.get(101)
    assert set(provider.metadata.tables) == {"content_base_analysis"}
    assert result.content_id == 101
    assert result.source == "Engineering Blog"
    assert result.language == "en"
    assert result.title == "AI coding agents"
    assert result.text == "Agent workflow details."
    assert result.summary == "How to test AI agents."
    assert result.key_points == ["Bound retries"]
    assert result.entities == ["Arq"]
    assert result.tags == ["agent-engineering"]
    assert result.embedding == [0.1, 1.0, -0.3]
    assert result.exposure == 42
    assert set(statements) <= {"SELECT", "PRAGMA"}


def test_snapshot_source_url_fallback_empty_title_and_language_fallback(snapshot_database):
    engine, table = snapshot_database
    row = snapshot_row()
    row["input_snapshot"]["title"] = ""
    row["input_snapshot"]["metadata"] = {"lang": "zh"}
    row["analysis"]["lang"] = None
    with engine.begin() as conn:
        conn.execute(update(table).values(**row))
    analysis = SqlAlchemyAnalysisProvider(engine).get(101)
    assert analysis.title == ""
    assert analysis.source == "https://example.test/agents"
    assert analysis.language == "zh"
    assert analysis.exposure == 0


@pytest.mark.parametrize("field_path,bad_value", [
    (("schema_version",), 2), (("schema_version",), None),
    (("status",), "FAILED"), (("status",), "CANCELLED"),
    (("input_snapshot", "content_id"), "102"), (("analysis", "content_id"), "102"),
    (("analysis", "content_id"), "demo-article"),
    (("analysis", "status"), "FAILED"),
    (("input_snapshot", "body"), "  "), (("input_snapshot", "body"), None),
    (("input_snapshot", "title"), None), (("input_snapshot", "metadata"), []),
    (("analysis", "summary"), ""), (("analysis", "key_points"), []),
    (("analysis", "base_tags"), []), (("analysis", "base_tags"), [4]),
    (("analysis", "entities"), "Arq"), (("analysis", "embedding"), []),
    (("analysis", "embedding"), [float("inf")]), (("analysis", "embedding"), [float("nan")]),
    (("analysis", "embedding"), [True]), (("analysis", "embedding"), ["0.1"]),
    (("input_snapshot", "metadata", "exposure"), -1), (("graph_version",), ""),
    (("run_id",), None), (("content_hash",), ""), (("analysis",), None),
])
def test_snapshot_rejects_invalid_or_unready_records(snapshot_database, field_path, bad_value):
    engine, table = snapshot_database
    row = deepcopy(snapshot_row())
    target = row
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value
    with engine.begin() as conn:
        conn.execute(update(table).values(**row))
    with pytest.raises((ValueError, TypeError)):
        SqlAlchemyAnalysisProvider(engine).get(101)


@pytest.mark.parametrize("bad_id", [True, 0, -1, 1.2, "00101", "demo-article", 2**63])
def test_snapshot_rejects_ids_not_representable_by_l2(snapshot_database, bad_id):
    with pytest.raises((ValueError, TypeError)):
        SqlAlchemyAnalysisProvider(snapshot_database[0]).get(bad_id)


def test_missing_snapshot_row_raises(snapshot_database):
    with pytest.raises(KeyError, match="L1 base_analysis not found"):
        SqlAlchemyAnalysisProvider(snapshot_database[0]).get(404)


def test_partial_snapshot_schema_does_not_fall_back_to_legacy():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    Table("content_base_analysis", metadata, Column("content_id", String), Column("schema_version", Integer))
    metadata.create_all(engine)
    with pytest.raises(ValueError, match="incomplete L1 snapshot table contract"):
        SqlAlchemyAnalysisProvider(engine)
