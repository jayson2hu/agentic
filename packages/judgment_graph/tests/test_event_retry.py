from typing import Any

import pytest
from judgment_graph.contracts import BaseAnalysis
from judgment_graph.events.consume import EventConsumer
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.repository import InMemoryJudgmentRepository


class UnavailableOnceProvider(StubAnalysisProvider):
    attempts = 0

    def get(self, content_id: int) -> BaseAnalysis:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("L1 temporarily unavailable")
        return super().get(content_id)


class InterruptedOnceLLM(FakeLLM):
    interrupted = False

    def complete_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        if task == "translate" and not self.interrupted:
            self.interrupted = True
            raise RuntimeError("translation temporarily unavailable")
        return super().complete_json(task, payload)


def test_retry_after_provider_failure_then_successful_duplicate_is_ignored() -> None:
    provider = UnavailableOnceProvider()
    repository = InMemoryJudgmentRepository()
    consumer = EventConsumer(provider, FileLensLoader(), FakeLLM(), repository)

    with pytest.raises(ConnectionError, match="L1 temporarily unavailable"):
        consumer.consume("content.analyzed", {"content_id": 1001})
    consumer.consume("content.analyzed", {"content_id": 1001})
    consumer.consume("content.analyzed", {"content_id": 1001})

    assert provider.attempts == 2
    assert repository.statuses[1001] == "COMPLETED"
    assert len(repository.outbox) == 1


def test_retry_after_partial_pipeline_write_completes_all_products() -> None:
    repository = InMemoryJudgmentRepository()
    consumer = EventConsumer(
        StubAnalysisProvider(), FileLensLoader(), InterruptedOnceLLM(), repository
    )

    with pytest.raises(RuntimeError, match="translation temporarily unavailable"):
        consumer.consume("content.analyzed", {"content_id": 1001})
    assert (1001, "ai-coding") in repository.content_vertical_scores
    assert repository.statuses[1001] == "WAIT_SCORE"
    assert repository.outbox == []

    consumer.consume("content.analyzed", {"content_id": 1001})
    assert repository.statuses[1001] == "COMPLETED"
    assert len(repository.content_vertical_scores) == 1
    assert set(repository.content_translations) == {(1001, "zh"), (1001, "en")}
    assert len(repository.outbox) == 1
