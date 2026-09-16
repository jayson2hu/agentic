from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from judgment_graph.contracts import BaseAnalysis


class AnalysisProvider(Protocol):
    def get(self, content_id: int) -> BaseAnalysis: ...

    def get_for_run(self, content_id: int, run_id: str) -> BaseAnalysis: ...


@dataclass(frozen=True)
class ContentDocument:
    analysis: BaseAnalysis
    url: str
    published_at: datetime
    quotes: list[str]
    thumbnail: str | None = None


class ContentDocumentProvider(AnalysisProvider, Protocol):
    def get_document(self, content_id: int) -> ContentDocument: ...
