from __future__ import annotations

import json
from pathlib import Path

from judgment_graph.contracts import BaseAnalysis


class StubAnalysisProvider:
    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path or Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "analysis" / "base_analysis.json"
        raw = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self._items = {int(item["content_id"]): BaseAnalysis(**item) for item in raw}

    def get(self, content_id: int) -> BaseAnalysis:
        try:
            return self._items[content_id]
        except KeyError as exc:
            raise KeyError(f"stub base_analysis not found: {content_id}") from exc

