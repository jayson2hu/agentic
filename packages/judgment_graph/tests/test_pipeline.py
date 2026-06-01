from judgment_graph.events.consume import EventConsumer
from judgment_graph.graph.companion import companion
from judgment_graph.graph.recommend import recommend
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.repository import InMemoryJudgmentRepository


def build_consumer() -> tuple[EventConsumer, InMemoryJudgmentRepository, StubAnalysisProvider]:
    provider = StubAnalysisProvider()
    repository = InMemoryJudgmentRepository()
    consumer = EventConsumer(provider, FileLensLoader(), FakeLLM(), repository)
    return consumer, repository, provider


def test_content_analyzed_pipeline_is_idempotent_and_persists_products() -> None:
    consumer, repository, _provider = build_consumer()
    consumer.consume("content.analyzed", {"content_id": 1001})
    consumer.consume("content.analyzed", {"content_id": 1001})

    score = repository.content_vertical_scores[(1001, "ai-coding")]
    assert score.quality_score > 70
    assert set(score.dim_scores) == {"topic", "content", "depth", "practical", "novelty", "expression"}
    assert all(0 <= value <= 100 for value in score.dim_scores.values())
    assert set(score.vertical_tags) <= {"agent-engineering", "llm-apps", "developer-tools", "workflow", "evaluation", "intro", "intermediate", "advanced", "tutorial", "case-study", "research", "tool-release"}
    assert (1001, "zh") in repository.content_translations
    assert (1001, "en") in repository.content_translations
    assert repository.statuses[1001] == "COMPLETED"
    assert len(repository.outbox) == 1


def test_non_relevant_content_is_cancelled() -> None:
    consumer, repository, _provider = build_consumer()
    consumer.consume("content.analyzed", {"content_id": 1002})
    assert repository.statuses[1002] == "CANCELLED"
    assert not repository.content_vertical_scores


def test_high_exposure_content_enters_review_and_decision_completes() -> None:
    consumer, repository, _provider = build_consumer()
    consumer.consume("content.analyzed", {"content_id": 1003})

    assert repository.statuses[1003] == "WAIT_REVIEW"
    [review] = list(repository.review_queue.values())
    assert review.reason == "high_exposure"

    repository.decide_review(review.id, "approved", reviewer="owner", note="approved gold sample")
    assert repository.statuses[1003] == "COMPLETED"
    assert repository.content_vertical_scores[(1003, "ai-coding")].reviewed is True
    assert any(event.type == "content.completed" for event in repository.outbox)


def test_recommend_and_companion_are_deterministic() -> None:
    consumer, repository, provider = build_consumer()
    consumer.consume("content.analyzed", {"content_id": 1001})

    refs = recommend(
        user_id=42,
        vertical="ai-coding",
        limit=3,
        repository=repository,
        user_tag_weights={42: {"agent-engineering": 2.0}},
    )
    assert [ref.content_id for ref in refs] == [1001]

    chunks = list(companion(1001, "How do I test this?", provider, FakeLLM()))
    assert chunks[0].startswith("data:")
    assert "How do I test this?" in "".join(chunks)

