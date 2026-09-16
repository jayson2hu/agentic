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
    _seen: set[str] = field(default_factory=set)

    def consume(self, event_type: str, payload: dict[str, object]) -> None:
        if event_type != "content.analyzed":
            raise ValueError(f"unsupported event: {event_type}")
        content_value = payload.get("content_id")
        if isinstance(content_value, bool) or not isinstance(
            content_value, (int, str)
        ):
            raise TypeError("content.analyzed content_id must be an integer")
        try:
            content_id = int(content_value)
        except ValueError as exc:
            raise ValueError(
                "content.analyzed content_id must be an integer"
            ) from exc
        run_value = payload.get("run_id")
        revision_value = payload.get("revision")
        if (run_value is None) != (revision_value is None):
            raise ValueError("content.analyzed requires run_id and revision together")
        source_run_id: str | None = None
        source_revision: int | None = None
        if run_value is not None and revision_value is not None:
            if not isinstance(run_value, str) or not run_value.strip():
                raise ValueError("content.analyzed run_id must be a non-empty string")
            if (
                isinstance(revision_value, bool)
                or not isinstance(revision_value, int)
                or revision_value < 1
            ):
                raise ValueError("content.analyzed revision must be a positive integer")
            source_run_id = run_value
            source_revision = revision_value
        identity = source_run_id or f"legacy:{content_id}"
        if identity in self._seen:
            return
        run_content_pipeline(
            content_id=content_id,
            vertical=self.vertical,
            provider=self.provider,
            lens_loader=self.lens_loader,
            llm=self.llm,
            repository=self.repository,
            source_run_id=source_run_id,
            source_revision=source_revision,
        )
        self._seen.add(identity)
