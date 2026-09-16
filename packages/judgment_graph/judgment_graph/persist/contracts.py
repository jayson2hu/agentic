from __future__ import annotations

from typing import Literal, Protocol

from judgment_graph.contracts import (
    ContentRef,
    ContentStatus,
    ReviewItem,
    Translation,
    VerticalScore,
)


class JudgmentRepository(Protocol):
    def get_status(self, content_id: int) -> ContentStatus | None: ...

    def set_status(self, content_id: int, status: ContentStatus) -> None: ...

    def persist_score(self, score: VerticalScore) -> None: ...

    def persist_translation(self, translation: Translation) -> None: ...

    def enqueue_review(self, content_id: int, vertical_code: str, reason: str) -> ReviewItem: ...

    def mark_completed(self, content_id: int) -> None: ...

    def decide_review(
        self,
        review_id: int,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        note: str,
    ) -> None: ...

    def completed_scores(self, vertical: str | None = None) -> list[VerticalScore]: ...

    def reprocess_needed(self, vertical: str, rubric_version: str) -> list[int]: ...

    def as_content_ref(self, score: VerticalScore, rank_score: float) -> ContentRef: ...

