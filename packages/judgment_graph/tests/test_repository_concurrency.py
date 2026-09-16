"""Real SQLite races between independent repository connections."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import Any, Literal

import pytest
from judgment_graph.contracts import DIMENSIONS, VerticalScore
from judgment_graph.events.consume import EventConsumer
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import (
    ConcurrentJudgmentUpdateError,
    SqlAlchemyJudgmentRepository,
)
from sqlalchemy import create_engine, event, select


@pytest.fixture
def repositories(tmp_path: Path) -> Iterator[tuple[
    SqlAlchemyJudgmentRepository, SqlAlchemyJudgmentRepository,
]]:
    url = f"sqlite:///{(tmp_path / 'concurrency.db').as_posix()}"
    engines = [create_engine(url), create_engine(url)]
    first, second = [SqlAlchemyJudgmentRepository(engine) for engine in engines]
    first.create_schema()
    try:
        yield first, second
    finally:
        for engine in engines:
            engine.dispose()


def _commit_between_read_and_write(
    delayed: SqlAlchemyJudgmentRepository,
    statement_prefix: str,
    delayed_action: Callable[[], None],
    winning_action: Callable[[], None],
) -> list[Exception]:
    """Let the delayed transaction read, then commit its competitor before its write."""
    read_barrier = Barrier(2)
    release_write = Event()
    errors: list[Exception] = []
    paused = False

    def pause_write(conn, cursor, statement, parameters, context, executemany):
        nonlocal paused
        if not paused and statement.startswith(statement_prefix):
            paused = True
            read_barrier.wait(timeout=10)
            if not release_write.wait(10):
                raise TimeoutError("competing transaction did not finish")

    def run_delayed() -> None:
        try:
            delayed_action()
        except Exception as exc:  # noqa: BLE001 - assert worker failures in the test thread
            errors.append(exc)

    event.listen(delayed.engine, "before_cursor_execute", pause_write)
    thread = Thread(target=run_delayed, daemon=True)
    thread.start()
    try:
        read_barrier.wait(timeout=10)
        winning_action()
    finally:
        release_write.set()
        thread.join(timeout=10)
        event.remove(delayed.engine, "before_cursor_execute", pause_write)
    assert not thread.is_alive(), "delayed repository operation did not finish"
    return errors


def test_stale_state_update_cannot_reopen_completed_content(repositories) -> None:
    first, second = repositories
    first.set_status(7, "WAIT_SCORE")
    errors = _commit_between_read_and_write(
        second,
        "UPDATE content_judgment_state",
        lambda: second.set_status(7, "WAIT_REVIEW"),
        lambda: first.mark_completed(7),
    )

    assert len(errors) == 1
    assert isinstance(errors[0], ConcurrentJudgmentUpdateError)
    assert first.get_status(7) == second.get_status(7) == "COMPLETED"
    assert [item.type for item in first.outbox()] == ["content.completed"]
    with pytest.raises(ValueError, match="COMPLETED -> WAIT_REVIEW"):
        second.set_status(7, "WAIT_REVIEW")


def test_concurrent_first_status_insert_reports_retryable_conflict(repositories) -> None:
    first, second = repositories
    insert_barrier = Barrier(2)
    errors: list[Exception] = []

    def pause_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO content_judgment_state"):
            insert_barrier.wait(timeout=10)

    def initialize(repo: SqlAlchemyJudgmentRepository) -> None:
        try:
            repo.set_status(8, "WAIT_SCORE")
        except Exception as exc:  # noqa: BLE001 - assert worker failures in the test thread
            errors.append(exc)

    for repo in repositories:
        event.listen(repo.engine, "before_cursor_execute", pause_insert)
    threads = [Thread(target=initialize, args=(repo,), daemon=True) for repo in repositories]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        for repo in repositories:
            event.remove(repo.engine, "before_cursor_execute", pause_insert)
    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 1
    assert isinstance(errors[0], ConcurrentJudgmentUpdateError)
    assert first.statuses == {8: "WAIT_SCORE"}
    assert first.cost_units == {8: 0}
    assert first.outbox() == []
    # A new transaction sees the winning insert and can retry the same legal state.
    second.set_status(8, "WAIT_SCORE")
    assert second.statuses == {8: "WAIT_SCORE"}


def test_old_revision_cannot_write_after_new_revision_is_accepted(repositories) -> None:
    first, second = repositories
    assert first.begin_version(7, "run-v1", 1)
    assert second.begin_version(7, "run-v2", 2)

    with pytest.raises(ConcurrentJudgmentUpdateError, match="newer source revision"):
        first.persist_score(
            VerticalScore(
                content_id=7,
                vertical_code="ai-coding",
                relevance=90,
                dim_scores={dimension: 90 for dimension in DIMENSIONS},
                vertical_tags=["agent-engineering"],
                quality_score=90,
                reviewed=False,
                rubric_version="aic-v1",
                model="late-model",
            ),
            source_revision=1,
        )
    assert second.get_status(7) == "WAIT_SCORE"
    assert second.cost_units == {7: 0}


@pytest.mark.parametrize("late_decision", ["approved", "rejected"])
def test_stale_review_decision_preserves_committed_reviewer_and_products(
    repositories, late_decision: Literal["approved", "rejected"],
) -> None:
    first, second = repositories
    first.set_status(9, "WAIT_SCORE")
    first.persist_score(VerticalScore(
        content_id=9, vertical_code="ai-coding", relevance=90,
        dim_scores={dimension: 90 for dimension in DIMENSIONS},
        vertical_tags=["agent-engineering"], quality_score=90, reviewed=False,
        rubric_version="aic-v1", model="test-model",
    ))
    review = first.enqueue_review(9, "ai-coding", "manual review")
    errors = _commit_between_read_and_write(
        second,
        "UPDATE review_queue",
        lambda: second.decide_review(review.id, late_decision, "late-reviewer", "late-note"),
        lambda: first.decide_review(review.id, "approved", "first-reviewer", "first-note"),
    )

    assert len(errors) == 1
    assert isinstance(errors[0], ConcurrentJudgmentUpdateError)
    with first.engine.connect() as connection:
        row = connection.execute(select(models.review_queue)).mappings().one()
    assert row["status"] == "approved"
    assert row["reviewer"] == "first-reviewer"
    assert row["decided_at"] is not None
    assert first.get_status(9) == "COMPLETED"
    assert first.completed_scores("ai-coding")[0].review_note == "first-note"
    assert first.cost_units == {9: 1}
    assert [item.type for item in first.outbox()] == ["content.completed"]
    # An idempotent repeat of the accepted decision cannot replace its audit fields.
    second.decide_review(review.id, "approved", "another-reviewer", "another-note")
    with first.engine.connect() as connection:
        assert connection.execute(select(models.review_queue.c.reviewer)).scalar_one() == (
            "first-reviewer"
        )
    assert first.completed_scores("ai-coding")[0].review_note == "first-note"

def test_late_duplicate_cannot_overwrite_completed_products(repositories) -> None:
    first, second = repositories
    model_barrier = Barrier(2)
    resume_model = Event()
    errors: list[Exception] = []

    class DelayedLLM(FakeLLM):
        def complete_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
            if task == "score_6dim":
                model_barrier.wait(timeout=10)
                if not resume_model.wait(10):
                    raise TimeoutError("competing content pipeline did not finish")
            result = super().complete_json(task, payload)
            if task == "refine":
                return {
                    **result,
                    "quality_score": 95,
                    "dim_scores": {dimension: 95 for dimension in DIMENSIONS},
                }
            return result

    delayed = EventConsumer(StubAnalysisProvider(), FileLensLoader(), DelayedLLM(), second)

    def run_delayed() -> None:
        try:
            delayed.consume("content.analyzed", {"content_id": 1001})
        except Exception as exc:  # noqa: BLE001 - assert worker failures in the test thread
            errors.append(exc)

    thread = Thread(target=run_delayed, daemon=True)
    thread.start()
    try:
        model_barrier.wait(timeout=10)
        EventConsumer(StubAnalysisProvider(), FileLensLoader(), FakeLLM(), first).consume(
            "content.analyzed", {"content_id": 1001},
        )
        scores_before = first.completed_scores("ai-coding")
        translations_before = {
            language: first.translation(1001, language) for language in ("en", "zh")
        }
        events_before = first.outbox()
        costs_before = first.cost_units
        assert first.get_status(1001) == "COMPLETED"
        assert len(scores_before) == 1
        assert scores_before[0].quality_score == 83
        assert all(translation is not None for translation in translations_before.values())
        assert costs_before == {1001: 3}
        assert len(events_before) == 1
    finally:
        resume_model.set()
        thread.join(timeout=10)

    assert not thread.is_alive(), "late duplicate did not finish"
    assert len(errors) == 1
    assert isinstance(errors[0], ConcurrentJudgmentUpdateError)
    assert first.get_status(1001) == second.get_status(1001) == "COMPLETED"
    assert first.completed_scores("ai-coding") == scores_before
    assert {
        language: first.translation(1001, language) for language in ("en", "zh")
    } == translations_before
    assert first.cost_units == costs_before
    assert first.outbox() == events_before
