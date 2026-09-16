from __future__ import annotations

from dataclasses import dataclass, field

from judgment_graph.graph.build import run_content_pipeline
from judgment_graph.input.provider import AnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import LLMClient
from judgment_graph.persist.contracts import JudgmentRepository


@dataclass
class EventConsumer:
    provider: AnalysisProvider
    lens_loader: FileLensLoader
    llm: LLMClient
    repository: JudgmentRepository
    vertical: str = "ai-coding"
    _seen: set[int] = field(default_factory=set)

    def consume(self, event_type: str, payload: dict[str, int]) -> None:
        if event_type != "content.analyzed":
            raise ValueError(f"unsupported event: {event_type}")
        content_id = int(payload["content_id"])
        if content_id in self._seen:
            return
        run_content_pipeline(
            content_id=content_id,
            vertical=self.vertical,
            provider=self.provider,
            lens_loader=self.lens_loader,
            llm=self.llm,
            repository=self.repository,
        )
        self._seen.add(content_id)
