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
        self.source_runs: dict[int, str] = {}
        self.source_revisions: dict[int, int] = {}
        self._review_id = 1

    def begin_version(
        self, content_id: int, source_run_id: str, source_revision: int
    ) -> bool:
        if not source_run_id.strip() or source_revision < 1:
            raise ValueError("versioned processing requires a run_id and positive revision")
        current = self.source_revisions.get(content_id, 0)
        if source_revision < current:
            return False
        if source_revision == current:
            if self.source_runs.get(content_id) != source_run_id:
                raise ValueError("source revision is already associated with another run")
            return False
        self.source_runs[content_id] = source_run_id
        self.source_revisions[content_id] = source_revision
        self.statuses[content_id] = "WAIT_SCORE"
        self.cost_units[content_id] = 0
        self.review_queue = {
            key: item
            for key, item in self.review_queue.items()
            if item.content_id != content_id or item.status != "pending"
        }
        return True

    def _check_revision(self, content_id: int, source_revision: int | None) -> None:
        if (
            source_revision is not None
            and self.source_revisions.get(content_id) != source_revision
        ):
            raise RuntimeError("a newer source revision is already active")

    def get_status(self, content_id: int) -> ContentStatus | None:
        return self.statuses.get(content_id)

    def set_status(
        self,
        content_id: int,
        status: ContentStatus,
        *,
        source_revision: int | None = None,
    ) -> None:
        self._check_revision(content_id, source_revision)
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

    def persist_score(
        self, score: VerticalScore, *, source_revision: int | None = None
    ) -> None:
        self._check_revision(score.content_id, source_revision)
        self.content_vertical_scores[(score.content_id, score.vertical_code)] = score
        self.cost_units[score.content_id] = self.cost_units.get(score.content_id, 0) + 1

    def persist_translation(
        self, translation: Translation, *, source_revision: int | None = None
    ) -> None:
        self._check_revision(translation.content_id, source_revision)
        self.content_translations[(translation.content_id, translation.lang)] = translation
        self.cost_units[translation.content_id] = self.cost_units.get(translation.content_id, 0) + 1

    def enqueue_review(
        self,
        content_id: int,
        vertical_code: str,
        reason: str,
        *,
        source_revision: int | None = None,
    ) -> ReviewItem:
        self._check_revision(content_id, source_revision)
        for item in self.review_queue.values():
            if (
                item.content_id == content_id
                and item.vertical_code == vertical_code
                and item.status == "pending"
            ):
                self.set_status(
                    content_id, "WAIT_REVIEW", source_revision=source_revision
                )
                return item
        item = ReviewItem(
            id=self._review_id,
            content_id=content_id,
            vertical_code=vertical_code,
            reason=reason,
        )
        self._review_id += 1
        self.review_queue[item.id] = item
        self.set_status(content_id, "WAIT_REVIEW", source_revision=source_revision)
        return item

    def mark_completed(
        self, content_id: int, *, source_revision: int | None = None
    ) -> None:
        self._check_revision(content_id, source_revision)
        self.set_status(content_id, "COMPLETED", source_revision=source_revision)
        revision = self.source_revisions.get(content_id, 0)
        payload: dict[str, object] = {"content_id": content_id}
        run_id = self.source_runs.get(content_id)
        if revision:
            payload["source_revision"] = revision
        if run_id is not None:
            payload["source_run_id"] = run_id
        if not any(
            event.type == "content.completed"
            and event.payload.get("content_id") == content_id
            and event.payload.get("source_revision", 0) == revision
            for event in self.outbox
        ):
            self.outbox.append(OutboxEvent(type="content.completed", payload=payload))

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
            self.mark_completed(
                item.content_id,
                source_revision=self.source_revisions.get(item.content_id),
            )
        else:
            self.set_status(
                item.content_id,
                "CANCELLED",
                source_revision=self.source_revisions.get(item.content_id),
            )

    def completed_scores(self, vertical: str | None = None) -> list[VerticalScore]:
        return [
            score
            for score in self.content_vertical_scores.values()
            if (vertical is None or score.vertical_code == vertical)
            and self.statuses.get(score.content_id) == "COMPLETED"
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
