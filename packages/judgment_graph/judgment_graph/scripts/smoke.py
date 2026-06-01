from __future__ import annotations

from judgment_graph.events.consume import EventConsumer
from judgment_graph.graph.companion import companion
from judgment_graph.graph.recommend import recommend
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.repository import InMemoryJudgmentRepository


def main() -> None:
    provider = StubAnalysisProvider()
    repository = InMemoryJudgmentRepository()
    consumer = EventConsumer(provider, FileLensLoader(), FakeLLM(), repository)
    consumer.consume("content.analyzed", {"content_id": 1001})
    score = repository.content_vertical_scores[(1001, "ai-coding")]
    assert all(0 <= value <= 100 for value in score.dim_scores.values())
    assert (1001, "zh") in repository.content_translations
    assert (1001, "en") in repository.content_translations
    assert repository.statuses[1001] == "COMPLETED"
    assert any(event.type == "content.completed" for event in repository.outbox)
    recs = recommend(
        user_id=1,
        vertical="ai-coding",
        limit=5,
        repository=repository,
        user_tag_weights={1: {"agent-engineering": 1.0}},
    )
    assert recs and recs[0].content_id == 1001
    chunks = list(companion(1001, "What should I learn?", provider, FakeLLM()))
    assert chunks and chunks[0].startswith("data:")
    print("L2 PIPELINE: PASS")


if __name__ == "__main__":
    main()

