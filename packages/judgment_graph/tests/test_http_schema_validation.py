from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from judgment_graph import http_api
from judgment_graph.http_api import ConfigurationError, create_app, service_from_environment
from judgment_graph.persist import models
from sqlalchemy import Column, MetaData, Table, create_engine, inspect
from sqlalchemy.exc import OperationalError
from test_http_api import seed_l1

HTTP_TABLES = (
    models.content_judgment_state,
    models.content_vertical_scores,
    models.content_translations,
)
HTTP_COLUMNS = [
    ("content_judgment_state", name)
    for name in ("content_id", "status", "source_run_id", "source_revision")
] + [
    (table.name, column.name)
    for table in HTTP_TABLES[1:]
    for column in table.columns
]


@pytest.fixture
def databases(tmp_path, monkeypatch):
    l1 = tmp_path / "l1.db"
    l2 = tmp_path / "l2.db"
    provider = seed_l1(f"sqlite:///{l1}", include=False)
    provider.engine.dispose()
    monkeypatch.setenv("L2_L1_DATABASE_URL", f"sqlite:///{l1}")
    monkeypatch.setenv("L2_DATABASE_URL", f"sqlite:///{l2}")
    monkeypatch.setenv("L2_PROCESSING_MODE", "heuristic")
    monkeypatch.delenv("L2_API_KEY", raising=False)
    engine = create_engine(f"sqlite:///{l2}")
    yield l1, l2, engine
    engine.dispose()


def create_read_schema(engine, *, missing_table=None, missing_columns=()) -> None:
    """Create only HTTP-owned read dependencies, not worker or review tables."""
    metadata = MetaData()
    for table in HTTP_TABLES:
        if table.name == missing_table:
            continue
        Table(
            table.name,
            metadata,
            *[
                Column(
                    column.name, column.type,
                    primary_key=column.primary_key, nullable=column.nullable,
                )
                for column in table.columns
                if (table.name, column.name) not in missing_columns
            ],
        )
    metadata.create_all(engine)


def assert_configuration_error(response, *expected: str) -> None:
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "configuration_error"
    assert detail["retryable"] is False
    assert "retry-after" not in response.headers
    for text in expected:
        assert text in detail["message"]


@pytest.mark.parametrize("table", [table.name for table in HTTP_TABLES])
def test_missing_http_table_is_non_retryable_configuration(databases, table) -> None:
    _l1, l2, engine = databases
    create_read_schema(engine, missing_table=table)
    before = l2.read_bytes()
    response = TestClient(create_app()).get("/content")
    assert_configuration_error(response, "missing table", table, "migrations")
    assert l2.read_bytes() == before
    assert table not in inspect(engine).get_table_names()


@pytest.mark.parametrize(("table", "column"), HTTP_COLUMNS)
def test_missing_http_column_is_non_retryable_configuration(databases, table, column) -> None:
    _l1, l2, engine = databases
    create_read_schema(engine, missing_columns={(table, column)})
    before = l2.read_bytes()
    response = TestClient(create_app()).get("/content")
    assert_configuration_error(response, table, "missing columns", column, "migrations")
    assert l2.read_bytes() == before
    assert column not in {item["name"] for item in inspect(engine).get_columns(table)}


@pytest.mark.parametrize("path", ["/content", "/content/1", "/recommend?user_id=1", "/companion?content_id=1"])
def test_legacy_state_without_version_columns_is_rejected_before_read(databases, path) -> None:
    _l1, l2, engine = databases
    create_read_schema(engine, missing_columns={
        ("content_judgment_state", "source_run_id"),
        ("content_judgment_state", "source_revision"),
    })
    before = l2.read_bytes()
    with pytest.raises(ConfigurationError, match="source_revision, source_run_id"):
        service_from_environment()
    response = TestClient(create_app()).get(path)
    assert_configuration_error(response, "source_run_id", "source_revision")
    assert l2.read_bytes() == before


def test_valid_read_only_databases_do_not_need_worker_tables(databases, monkeypatch) -> None:
    l1, l2, engine = databases
    create_read_schema(engine)
    before = {path: path.read_bytes() for path in (l1, l2)}
    monkeypatch.setenv("L2_L1_DATABASE_URL", f"sqlite:///file:{l1}?mode=ro&uri=true")
    monkeypatch.setenv("L2_DATABASE_URL", f"sqlite:///file:{l2}?mode=ro&uri=true")
    api = TestClient(create_app())
    assert api.get("/content").json() == {"items": [], "next_cursor": None, "total": 0}
    assert api.get("/recommend", params={"user_id": 1}).json() == {"items": []}
    assert api.get("/content/1").status_code == 404
    assert api.get("/companion", params={"content_id": 1}).status_code == 404
    assert set(inspect(engine).get_table_names()) == {table.name for table in HTTP_TABLES}
    assert all(path.read_bytes() == data for path, data in before.items())


def test_schema_failure_is_not_cached_after_external_repair(databases) -> None:
    _l1, _l2, engine = databases
    with engine.connect():
        pass
    api = TestClient(create_app())
    assert_configuration_error(api.get("/content"), "missing table")
    # A separate operator/test action creates the schema; the HTTP service never does.
    create_read_schema(engine)
    response = api.get("/content")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_inspection_connection_failure_remains_retryable_storage(databases, monkeypatch) -> None:
    _l1, l2, _engine = databases
    unavailable = l2.parent / "not-created" / "database.db"
    monkeypatch.setenv("L2_DATABASE_URL", f"sqlite:///{unavailable}")
    response = TestClient(create_app()).get("/content")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "storage_unavailable", "message": "L2 storage unavailable", "retryable": True,
    }
    assert response.headers["retry-after"] == "2"
    assert not unavailable.exists()


def test_storage_failure_after_successful_inspection_remains_retryable(databases, monkeypatch) -> None:
    _l1, _l2, engine = databases
    create_read_schema(engine)
    service = service_from_environment()
    monkeypatch.setattr(http_api, "service_from_environment", lambda: service)
    api = TestClient(create_app())
    assert api.get("/content").status_code == 200

    def disconnected(_vertical):
        raise OperationalError("select", {}, RuntimeError("connection lost"))

    monkeypatch.setattr(service.repository, "completed_scores", disconnected)
    response = api.get("/content")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert response.headers["retry-after"] == "2"


def test_l1_missing_schema_remains_configuration_with_valid_l2(databases, monkeypatch) -> None:
    l1, _l2, engine = databases
    create_read_schema(engine)
    monkeypatch.setenv("L2_L1_DATABASE_URL", f"sqlite:///{l1.parent / 'empty-l1.db'}")
    assert_configuration_error(TestClient(create_app()).get("/content"), "content_base_analysis")
