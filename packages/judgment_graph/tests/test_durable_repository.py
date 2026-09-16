from pathlib import Path

import pytest
from judgment_graph.graph.build import run_content_pipeline
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import create_engine, event, select


def run_fixture(repo: SqlAlchemyJudgmentRepository, content_id: int = 1001) -> None:
    run_content_pipeline(content_id, "ai-coding", StubAnalysisProvider(), FileLensLoader(), FakeLLM(), repo)


def test_pipeline_products_state_cost_and_outbox_survive_new_engine(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'l2.db'}"
    first_engine = create_engine(url)
    first = SqlAlchemyJudgmentRepository(first_engine)
    first.create_schema()
    run_fixture(first)
    assert first.cost_units[1001] == 3
    original_events = first.outbox()
    first_engine.dispose()

    second_engine = create_engine(url)
    second = SqlAlchemyJudgmentRepository(second_engine)
    assert second.get_status(1001) == "COMPLETED"
    assert second.statuses == {1001: "COMPLETED"}
    assert [score.content_id for score in second.completed_scores("ai-coding")] == [1001]
    assert second.translation(1001, "zh") is not None
    assert second.translation(1001, "en") is not None
    assert second.cost_units[1001] == 3
    assert second.outbox() == original_events
    second.mark_completed(1001)
    assert second.outbox() == original_events
    assert second.cost_units[1001] == 3
    with pytest.raises(ValueError, match="illegal status transition"):
        second.set_status(1001, "WAIT_SCORE")
    run_fixture(second)
    assert second.cost_units[1001] == 3
    assert second.get_status(404) is None
    second_engine.dispose()


def test_new_l1_revision_rescores_and_stale_redelivery_is_ignored(tmp_path: Path) -> None:
    from judgment_graph.events.consume import EventConsumer

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'versions.db'}")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()
    consumer = EventConsumer(
        StubAnalysisProvider(), FileLensLoader(), FakeLLM(), repo
    )

    consumer.consume(
        "content.analyzed",
        {"content_id": 1001, "run_id": "l1-run-v1", "revision": 1},
    )
    assert repo.get_status(1001) == "COMPLETED"
    assert repo.cost_units[1001] == 3
    consumer.consume(
        "content.analyzed",
        {"content_id": 1001, "run_id": "l1-run-v2", "revision": 2},
    )
    assert repo.get_status(1001) == "COMPLETED"
    assert repo.cost_units[1001] == 3
    assert [event.payload["source_revision"] for event in repo.outbox()] == [1, 2]

    scores = repo.completed_scores("ai-coding")
    translations = [repo.translation(1001, lang) for lang in ("en", "zh")]
    consumer.consume(
        "content.analyzed",
        {"content_id": 1001, "run_id": "late-l1-run-v1", "revision": 1},
    )
    assert repo.completed_scores("ai-coding") == scores
    assert [repo.translation(1001, lang) for lang in ("en", "zh")] == translations
    assert [event.payload["source_revision"] for event in repo.outbox()] == [1, 2]
    engine.dispose()


def test_review_can_complete_after_repository_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'review.db'}"
    engine = create_engine(url)
    first = SqlAlchemyJudgmentRepository(engine)
    first.create_schema()
    run_fixture(first, 1003)
    assert first.get_status(1003) == "WAIT_REVIEW"
    assert first.outbox() == []
    with engine.connect() as conn:
        review_id = conn.execute(select(models.review_queue.c.id)).scalar_one()
    engine.dispose()

    second_engine = create_engine(url)
    second = SqlAlchemyJudgmentRepository(second_engine)
    second.decide_review(review_id, "approved", "owner", "checked")
    second.decide_review(review_id, "approved", "owner", "duplicate")
    assert second.get_status(1003) == "COMPLETED"
    assert second.completed_scores("ai-coding")[0].review_note == "checked"
    assert len(second.outbox()) == 1
    with pytest.raises(ValueError, match="already final"):
        second.decide_review(review_id, "rejected", "owner", "conflict")
    second_engine.dispose()


def test_completion_state_rolls_back_if_outbox_insert_fails() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()
    repo.set_status(1001, "WAIT_SCORE")

    def fail_outbox(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO judgment_outbox"):
            raise RuntimeError("outbox unavailable")

    event.listen(engine, "before_cursor_execute", fail_outbox)
    try:
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            repo.mark_completed(1001)
    finally:
        event.remove(engine, "before_cursor_execute", fail_outbox)
    assert repo.get_status(1001) == "WAIT_SCORE"
    assert repo.outbox() == []
    repo.mark_completed(1001)
    assert repo.get_status(1001) == "COMPLETED"
    assert len(repo.outbox()) == 1


def test_product_and_cost_rollback_together_on_failed_translation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()

    def fail_translation(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO content_translations"):
            raise RuntimeError("translation write unavailable")

    event.listen(engine, "before_cursor_execute", fail_translation)
    try:
        with pytest.raises(RuntimeError, match="translation write unavailable"):
            run_fixture(repo)
    finally:
        event.remove(engine, "before_cursor_execute", fail_translation)
    assert repo.cost_units[1001] == 1
    assert repo.get_status(1001) == "WAIT_SCORE"
    assert repo.translation(1001, "zh") is None
    assert repo.outbox() == []
    run_fixture(repo)
    assert repo.cost_units[1001] == 4
    assert repo.get_status(1001) == "COMPLETED"
    assert len(repo.outbox()) == 1


@pytest.mark.parametrize("content_id,status", [(1001, "COMPLETED"), (1002, "CANCELLED"), (1003, "WAIT_REVIEW")])
def test_restarted_duplicate_event_and_worker_preserve_terminal_or_review_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_id: int, status: str,
) -> None:
    import asyncio
    from importlib import import_module
    from unittest.mock import Mock

    from judgment_graph.events.consume import EventConsumer

    url = f"sqlite+pysqlite:///{tmp_path / 'redelivery.db'}"
    first_engine = create_engine(url)
    first = SqlAlchemyJudgmentRepository(first_engine)
    first.create_schema()
    run_fixture(first, content_id)
    expected_events = first.outbox()
    expected_cost = first.cost_units
    first_engine.dispose()

    second_engine = create_engine(url)
    second = SqlAlchemyJudgmentRepository(second_engine)
    provider = Mock(spec=StubAnalysisProvider)
    llm = Mock(spec=FakeLLM)
    consumer = EventConsumer(provider, FileLensLoader(), llm, second)
    consumer.consume("content.analyzed", {"content_id": content_id})
    # The packaged worker and direct pipeline callers share the same durable short circuit.
    worker = import_module("judgment_graph.workers.scoring.worker")
    monkeypatch.setattr(worker, "consumer", EventConsumer(provider, FileLensLoader(), llm, second))
    asyncio.run(worker.score({}, content_id))
    run_content_pipeline(content_id, "ai-coding", provider, FileLensLoader(), llm, second)

    provider.get.assert_not_called()
    llm.complete_json.assert_not_called()
    assert second.get_status(content_id) == status
    assert second.outbox() == expected_events
    assert second.cost_units == expected_cost
    if status == "WAIT_REVIEW":
        with second_engine.connect() as conn:
            assert conn.execute(select(models.review_queue.c.status)).scalars().all() == ["pending"]
    second_engine.dispose()


def product_seed_score(content_id: int = 7):
    from judgment_graph.contracts import DIMENSIONS, VerticalScore

    return VerticalScore(
        content_id=content_id, vertical_code="ai-coding", relevance=90,
        dim_scores={dimension: 83 for dimension in DIMENSIONS}, vertical_tags=["agent-engineering"],
        quality_score=83, reviewed=False, rubric_version="aic-v1", model="fake-l2-model",
    )


@pytest.mark.parametrize("sealed_status", ["COMPLETED", "CANCELLED", "WAIT_REVIEW"])
def test_sealed_products_reject_both_score_and_translation_writes(sealed_status):
    from dataclasses import replace

    from judgment_graph.contracts import Translation
    from judgment_graph.persist.sqlalchemy_repository import ConcurrentJudgmentUpdateError

    engine = create_engine("sqlite+pysqlite:///:memory:")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()
    original_score = product_seed_score()
    original_translation = Translation(7, "zh", {"title": "Original"}, "fake-l2-model")
    repo.set_status(7, "WAIT_SCORE")
    repo.persist_score(original_score)
    repo.persist_translation(original_translation)
    if sealed_status == "COMPLETED":
        repo.mark_completed(7)
    elif sealed_status == "WAIT_REVIEW":
        repo.enqueue_review(7, "ai-coding", "manual check")
    else:
        repo.set_status(7, "CANCELLED")
    original_events = repo.outbox()
    with pytest.raises(ConcurrentJudgmentUpdateError, match="products are sealed"):
        repo.persist_score(replace(original_score, quality_score=95))
    with pytest.raises(ConcurrentJudgmentUpdateError, match="products are sealed"):
        repo.persist_translation(replace(original_translation, fields={"title": "Late overwrite"}))
    with engine.connect() as conn:
        assert conn.execute(select(models.content_vertical_scores.c.quality_score)).scalar_one() == 83
    assert repo.translation(7, "zh") == original_translation
    assert repo.get_status(7) == sealed_status
    assert repo.cost_units[7] == 2
    assert repo.outbox() == original_events
    engine.dispose()


def test_product_seeds_without_initial_status_remain_supported() -> None:
    from judgment_graph.contracts import Translation

    engine = create_engine("sqlite+pysqlite:///:memory:")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()
    repo.persist_score(product_seed_score())
    repo.persist_translation(Translation(7, "en", {"title": "Seed"}, "fake-l2-model"))
    assert repo.get_status(7) is None
    assert repo.cost_units[7] == 2
    repo.set_status(7, "WAIT_SCORE")
    repo.mark_completed(7)
    assert repo.completed_scores("ai-coding")[0].quality_score == 83
    assert repo.translation(7, "en").fields["title"] == "Seed"
    engine.dispose()
