from __future__ import annotations

from typing import Protocol

from judgment_graph.contracts import BaseAnalysis


class AnalysisProvider(Protocol):
    def get(self, content_id: int) -> BaseAnalysis: ...

