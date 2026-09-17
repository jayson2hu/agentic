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
    def begin_version(
        self, content_id: int, source_run_id: str, source_revision: int
    ) -> bool: ...

    def get_status(self, content_id: int) -> ContentStatus | None: ...

    def get_source_version(self, content_id: int) -> tuple[str, int] | None: ...

    def set_status(
        self,
        content_id: int,
        status: ContentStatus,
        *,
        source_revision: int | None = None,
    ) -> None: ...

    def persist_score(
        self, score: VerticalScore, *, source_revision: int | None = None
    ) -> None: ...

    def persist_translation(
        self, translation: Translation, *, source_revision: int | None = None
    ) -> None: ...

    def enqueue_review(
        self,
        content_id: int,
        vertical_code: str,
        reason: str,
        *,
        source_revision: int | None = None,
    ) -> ReviewItem: ...

    def mark_completed(
        self, content_id: int, *, source_revision: int | None = None
    ) -> None: ...

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
