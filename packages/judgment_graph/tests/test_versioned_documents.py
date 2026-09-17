from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from judgment_graph.contracts import DIMENSIONS, Translation, VerticalScore
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider
from judgment_graph.persist.repository import InMemoryJudgmentRepository
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    insert,
    select,
    update,
)
from test_http_api import seed_l1


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        yield InMemoryJudgmentRepository()
    else:
        repository = SqlAlchemyJudgmentRepository(create_engine(f"sqlite:///{tmp_path / 'l2.db'}"))
        repository.create_schema()
        yield repository
        repository.engine.dispose()


def score(content_id: int = 1, vertical: str = "ai-coding") -> VerticalScore:
    return VerticalScore(
        content_id=content_id,
        vertical_code=vertical,
        relevance=91,
        dim_scores={dimension: 84 for dimension in DIMENSIONS},
        vertical_tags=["agent-engineering"],
        quality_score=89,
        reviewed=False,
        rubric_version="aic-v1",
        model="fake-l2-model",
    )


def translation(content_id: int = 1) -> Translation:
    return Translation(
        content_id=content_id, lang="zh", fields={"summary": "old simulated translation"},
        model="fake-l2-model",
    )


def read_translation(repository, content_id: int = 1) -> Translation | None:
    if isinstance(repository, InMemoryJudgmentRepository):
        return repository.content_translations.get((content_id, "zh"))
    return repository.translation(content_id, "zh")


def seed_products(repository, content_id: int = 1) -> None:
    assert repository.begin_version(content_id, f"run-{content_id}-v1", 1)
    repository.persist_score(score(content_id), source_revision=1)
    repository.persist_score(score(content_id, "other-vertical"), source_revision=1)
    repository.persist_translation(translation(content_id), source_revision=1)
    repository.mark_completed(content_id, source_revision=1)


def test_new_revision_invalidates_all_old_products_only_for_its_content(repository) -> None:
    seed_products(repository)
    seed_products(repository, 2)

    assert repository.begin_version(1, "run-1-v2", 2)
    assert repository.get_source_version(1) == ("run-1-v2", 2)
    assert repository.get_status(1) == "WAIT_SCORE"
    assert repository.cost_units[1] == 0
    assert read_translation(repository) is None
    assert read_translation(repository, 2) == translation(2)

    new_score = replace(score(), model="heuristic-v1")
    repository.persist_score(new_score, source_revision=2)
    repository.mark_completed(1, source_revision=2)

    assert [item for item in repository.completed_scores() if item.content_id == 1] == [new_score]
    assert len([item for item in repository.completed_scores() if item.content_id == 2]) == 2
    assert repository.cost_units[1] == 1


def test_duplicate_and_stale_versions_cannot_clear_new_products(repository) -> None:
    seed_products(repository)
    assert repository.begin_version(1, "run-1-v2", 2)
    new_score = replace(score(), model="heuristic-v1")
    repository.persist_score(new_score, source_revision=2)
    new_translation = replace(translation(), fields={"summary": "new translation"})
    repository.persist_translation(new_translation, source_revision=2)
    repository.mark_completed(1, source_revision=2)

    assert not repository.begin_version(1, "run-1-v2", 2)
    assert not repository.begin_version(1, "run-1-v1", 1)
    with pytest.raises(ValueError, match="another run"):
        repository.begin_version(1, "different-run", 2)
    with pytest.raises(RuntimeError, match="newer source revision"):
        repository.persist_score(score(), source_revision=1)
    with pytest.raises(RuntimeError, match="newer source revision"):
        repository.persist_translation(translation(), source_revision=1)

    assert repository.get_source_version(1) == ("run-1-v2", 2)
    assert repository.completed_scores() == [new_score]
    assert read_translation(repository) == new_translation
    assert repository.cost_units[1] == 2


def test_legacy_and_missing_records_have_no_source_version(repository) -> None:
    assert repository.get_source_version(404) is None
    repository.set_status(1, "WAIT_SCORE")
    repository.persist_score(score())
    repository.mark_completed(1)
    assert repository.get_source_version(1) is None
    assert repository.begin_version(1, "run-1-v1", 1)
    assert repository.get_source_version(1) == ("run-1-v1", 1)
    assert repository.completed_scores() == []


def test_sql_version_and_cleanup_rollback_together(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'l2.db'}")
    repository = SqlAlchemyJudgmentRepository(engine)
    repository.create_schema()
    seed_products(repository)

    def fail_translation_cleanup(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("delete from content_translations"):
            raise RuntimeError("injected cleanup failure")

    event.listen(engine, "before_cursor_execute", fail_translation_cleanup)
    try:
        with pytest.raises(RuntimeError, match="injected cleanup failure"):
            repository.begin_version(1, "run-1-v2", 2)
    finally:
        event.remove(engine, "before_cursor_execute", fail_translation_cleanup)

    reopened = SqlAlchemyJudgmentRepository(create_engine(f"sqlite:///{tmp_path / 'l2.db'}"))
    assert reopened.get_source_version(1) == ("run-1-v1", 1)
    assert reopened.get_status(1) == "COMPLETED"
    assert len(reopened.completed_scores()) == 2
    assert read_translation(reopened) == translation()
    assert reopened.cost_units[1] == 3
    reopened.engine.dispose()
    engine.dispose()


@pytest.fixture
def historical_provider(tmp_path):
    provider = seed_l1(f"sqlite:///{tmp_path / 'l1.db'}")
    runs = Table(
        "l1_processing_runs", MetaData(),
        Column("content_id", String, nullable=False),
        Column("run_id", String, primary_key=True),
        Column("input_snapshot", JSON, nullable=False),
        Column("analysis", JSON, nullable=False),
        Column("graph_version", String, nullable=False),
        Column("content_hash", String, nullable=False),
        Column("status", String, nullable=False),
        Column("finished_at", DateTime(timezone=True), nullable=False),
    )
    runs.create(provider.engine)
    current = provider.content_base_analysis
    with provider.engine.begin() as connection:
        row = dict(connection.execute(select(current)).mappings().one())
        historical = {
            key: deepcopy(row[key]) for key in (
                "content_id", "run_id", "input_snapshot", "analysis",
                "graph_version", "content_hash", "status",
            )
        }
        historical["finished_at"] = row["updated_at"]
        connection.execute(insert(runs).values(**historical))

        latest_snapshot = deepcopy(row["input_snapshot"])
        latest_snapshot.update(
            title="Unjudged revision two",
            source_url="https://example.test/revision-two",
            published_at="2026-09-17T10:00:00+00:00",
        )
        latest_analysis = deepcopy(row["analysis"])
        latest_analysis.update(summary="Unjudged revision two summary", quotes=["Revision two quote"])
        connection.execute(update(current).values(
            run_id="run-2", input_snapshot=latest_snapshot, analysis=latest_analysis,
        ))
    yield SqlAlchemyAnalysisProvider(provider.engine)
    provider.engine.dispose()


def test_historical_document_keeps_title_source_quotes_and_date_from_accepted_run(historical_provider) -> None:
    latest = historical_provider.get_document(1)
    accepted = historical_provider.get_document_for_run(1, "run-1")

    assert latest.analysis.title == "Unjudged revision two"
    assert accepted.analysis.title == "M1 durable article"
    assert accepted.url == "https://example.test/m1"
    assert accepted.analysis.summary == "Persisted L1 summary"
    assert accepted.quotes == ["Persistence survives restarts."]
    assert accepted.published_at is not None
    assert accepted.published_at.isoformat() == "2026-09-12T08:00:00+00:00"
    assert historical_provider.get_for_run(1, "run-1") == accepted.analysis


def test_historical_document_never_falls_back_to_current_projection(historical_provider) -> None:
    with pytest.raises(KeyError, match="not found"):
        historical_provider.get_document_for_run(1, "missing-run")
    with pytest.raises(KeyError, match="not found"):
        historical_provider.get_document_for_run(2, "run-1")
    with pytest.raises(ValueError, match="required"):
        historical_provider.get_document_for_run(1, " ")
    with pytest.raises(ValueError, match="positive integer"):
        historical_provider.get_document_for_run(0, "run-1")


@pytest.mark.parametrize("published_at", [None, "", " "])
def test_unknown_publication_time_does_not_use_processing_time(historical_provider, published_at) -> None:
    provider = historical_provider
    assert provider.processing_runs is not None
    with provider.engine.begin() as connection:
        for table in (provider.content_base_analysis, provider.processing_runs):
            row = connection.execute(select(table)).mappings().one()
            snapshot = dict(row["input_snapshot"])
            snapshot["published_at"] = published_at
            connection.execute(update(table).values(input_snapshot=snapshot))

    assert provider.get_document(1).published_at is None
    assert provider.get_document_for_run(1, "run-1").published_at is None
