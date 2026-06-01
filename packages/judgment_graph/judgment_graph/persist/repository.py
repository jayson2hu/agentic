from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from judgment_graph.contracts import (
    ContentRef,
    ContentStatus,
    OutboxEvent,
    ReviewItem,
    Translation,
    VerticalScore,
)


class InMemoryJudgmentRepository:
    """Repository with table-shaped collections matching L2-owned products."""

    def __init__(self) -> None:
        self.content_vertical_scores: dict[tuple[int, str], VerticalScore] = {}
        self.content_translations: dict[tuple[int, str], Translation] = {}
        self.review_queue: dict[int, ReviewItem] = {}
        self.statuses: dict[int, ContentStatus] = {}
        self.outbox: list[OutboxEvent] = []
        self.cost_units: dict[int, int] = {}
        self._review_id = 1

    def set_status(self, content_id: int, status: ContentStatus) -> None:
        current = self.statuses.get(content_id)
        legal = {
            None: {"WAIT_SCORE", "CANCELLED"},
            "WAIT_SCORE": {"WAIT_REVIEW", "COMPLETED", "CANCELLED", "WAIT_SCORE"},
            "WAIT_REVIEW": {"COMPLETED", "CANCELLED", "WAIT_REVIEW"},
            "COMPLETED": {"COMPLETED"},
            "CANCELLED": {"CANCELLED"},
        }
        if status not in legal[current]:
            raise ValueError(f"illegal status transition: {current} -> {status}")
        self.statuses[content_id] = status

    def persist_score(self, score: VerticalScore) -> None:
        self.content_vertical_scores[(score.content_id, score.vertical_code)] = score
        self.cost_units[score.content_id] = self.cost_units.get(score.content_id, 0) + 1

    def persist_translation(self, translation: Translation) -> None:
        self.content_translations[(translation.content_id, translation.lang)] = translation
        self.cost_units[translation.content_id] = self.cost_units.get(translation.content_id, 0) + 1

    def enqueue_review(self, content_id: int, vertical_code: str, reason: str) -> ReviewItem:
        for item in self.review_queue.values():
            if (
                item.content_id == content_id
                and item.vertical_code == vertical_code
                and item.status == "pending"
            ):
                self.set_status(content_id, "WAIT_REVIEW")
                return item
        item = ReviewItem(
            id=self._review_id,
            content_id=content_id,
            vertical_code=vertical_code,
            reason=reason,
        )
        self._review_id += 1
        self.review_queue[item.id] = item
        self.set_status(content_id, "WAIT_REVIEW")
        return item

    def mark_completed(self, content_id: int) -> None:
        self.set_status(content_id, "COMPLETED")
        if not any(event.type == "content.completed" and event.payload == {"content_id": content_id} for event in self.outbox):
            self.outbox.append(OutboxEvent(type="content.completed", payload={"content_id": content_id}))

    def decide_review(
        self,
        review_id: int,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        note: str,
    ) -> None:
        item = self.review_queue[review_id]
        self.review_queue[review_id] = replace(
            item,
            status=decision,
            reviewer=reviewer,
            decided_at=datetime.now(UTC),
        )
        key = (item.content_id, item.vertical_code)
        score = self.content_vertical_scores[key]
        self.content_vertical_scores[key] = replace(score, reviewed=True, review_note=note)
        if decision == "approved":
            self.mark_completed(item.content_id)
        else:
            self.set_status(item.content_id, "CANCELLED")

    def completed_scores(self, vertical: str) -> list[VerticalScore]:
        return [
            score
            for score in self.content_vertical_scores.values()
            if score.vertical_code == vertical and self.statuses.get(score.content_id) == "COMPLETED"
        ]

    def reprocess_needed(self, vertical: str, rubric_version: str) -> list[int]:
        return [
            score.content_id
            for score in self.content_vertical_scores.values()
            if score.vertical_code == vertical and score.rubric_version != rubric_version
        ]

    def as_content_ref(self, score: VerticalScore, rank_score: float) -> ContentRef:
        return ContentRef(
            content_id=score.content_id,
            vertical_code=score.vertical_code,
            quality_score=score.quality_score,
            relevance=score.relevance,
            tags=score.vertical_tags,
            rank_score=rank_score,
        )

