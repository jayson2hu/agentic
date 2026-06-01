from judgment_graph.events.consume import EventConsumer
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.repository import InMemoryJudgmentRepository
from judgment_graph.service import companion, configure_service, recommend


def test_frozen_request_time_api_signatures_work_with_configured_dependencies() -> None:
    provider = StubAnalysisProvider()
    repo = InMemoryJudgmentRepository()
    consumer = EventConsumer(provider, FileLensLoader(), FakeLLM(), repo)
    consumer.consume("content.analyzed", {"content_id": 1001})
    configure_service(provider=provider, llm=FakeLLM(), repository=repo)

    assert recommend(1, "ai-coding", 1)[0].content_id == 1001
    assert "grounded in the L1 summary" in "".join(companion(1001, "Why read it?"))

