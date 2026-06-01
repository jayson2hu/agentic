from __future__ import annotations

from collections.abc import Iterator

from judgment_graph.contracts import ContentRef
from judgment_graph.graph.companion import companion as companion_graph
from judgment_graph.graph.recommend import recommend as recommend_graph
from judgment_graph.input.factory import create_analysis_provider
from judgment_graph.input.provider import AnalysisProvider
from judgment_graph.llm import FakeLLM, LLMClient
from judgment_graph.persist.contracts import JudgmentRepository
from judgment_graph.persist.repository import InMemoryJudgmentRepository

_provider: AnalysisProvider = create_analysis_provider()
_llm: LLMClient = FakeLLM()
_repository: JudgmentRepository = InMemoryJudgmentRepository()


def configure_service(
    *,
    provider: AnalysisProvider | None = None,
    llm: LLMClient | None = None,
    repository: JudgmentRepository | None = None,
) -> None:
    global _provider, _llm, _repository
    if provider is not None:
        _provider = provider
    if llm is not None:
        _llm = llm
    if repository is not None:
        _repository = repository


def repository() -> JudgmentRepository:
    return _repository


def recommend(user_id: int, vertical: str, limit: int) -> list[ContentRef]:
    return recommend_graph(user_id, vertical, limit, _repository)


def companion(content_id: int, question: str) -> Iterator[str]:
    return companion_graph(content_id, question, _provider, _llm)
