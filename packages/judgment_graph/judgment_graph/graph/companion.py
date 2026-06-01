from __future__ import annotations

from collections.abc import Iterator

from judgment_graph.input.provider import AnalysisProvider
from judgment_graph.llm import LLMClient


def companion(content_id: int, question: str, provider: AnalysisProvider, llm: LLMClient) -> Iterator[str]:
    analysis = provider.get(content_id)
    chunks = llm.stream_text(
        "companion",
        {
            "title": analysis.title,
            "summary": analysis.summary,
            "key_points": analysis.key_points,
            "question": question,
        },
    )
    yield from chunks

