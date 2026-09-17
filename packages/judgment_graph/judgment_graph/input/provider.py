from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from judgment_graph.contracts import BaseAnalysis


class AnalysisProvider(Protocol):
    def get(self, content_id: int) -> BaseAnalysis: ...

    def get_for_run(self, content_id: int, run_id: str) -> BaseAnalysis: ...


@dataclass(frozen=True)
class ContentDocument:
    analysis: BaseAnalysis
    url: str
    published_at: datetime | None
    quotes: list[str]
    thumbnail: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


class ContentDocumentProvider(AnalysisProvider, Protocol):
    def get_document(self, content_id: int) -> ContentDocument: ...

    def get_document_for_run(self, content_id: int, run_id: str) -> ContentDocument: ...
