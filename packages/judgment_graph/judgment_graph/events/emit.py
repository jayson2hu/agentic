from __future__ import annotations

from judgment_graph.persist.repository import InMemoryJudgmentRepository


def emit_completed(repository: InMemoryJudgmentRepository, content_id: int) -> None:
    repository.mark_completed(content_id)

