from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import Engine, Select, create_engine, delete, insert, select, update
from sqlalchemy.engine import RowMapping

from judgment_graph.contracts import (
    ContentRef,
    ContentStatus,
    OutboxEvent,
    ReviewItem,
    Translation,
    VerticalScore,
)
from judgment_graph.persist import models
from judgment_graph.contracts import Lens


def create_sqlalchemy_engine(url: str) -> Engine:
    return create_engine(url, future=True)


class SqlAlchemyJudgmentRepository:
    """SQLAlchemy implementation for L2-owned tables and outbox state."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.statuses: dict[int, ContentStatus] = {}
        self._outbox: list[OutboxEvent] = []
        self.cost_units: dict[int, int] = {}

    def create_schema(self) -> None:
        models.metadata.create_all(self.engine)

    def seed_lens(self, lens: dict[str, object]) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(models.verticals).where(models.verticals.c.code == lens["code"]))
            conn.execute(insert(models.verticals).values(**lens))

    def load_lens(self, vertical_code: str) -> Lens:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(models.verticals).where(models.verticals.c.code == vertical_code)
            ).mappings().one()
        return Lens(
            code=str(row["code"]),
            name=str(row["name"]),
            enabled=bool(row["enabled"]),
            source_filter=dict(row["source_filter"]),
            rubric_prompt=str(row["rubric_prompt"]),
            rubric_version=str(row["rubric_version"]),
            tag_taxonomy={
                str(key): [str(item) for item in value]
                for key, value in dict(row["tag_taxonomy"]).items()
            },
            brief_template=str(row["brief_template"]),
            model_profile=dict(row["model_profile"]),
        )

    def set_status(self, content_id: int, status: ContentStatus) -> None:
        current = self.get_status(content_id)
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

    def get_status(self, content_id: int) -> ContentStatus | None:
        return self.statuses.get(content_id)

    def persist_score(self, score: VerticalScore) -> None:
        values = {
            "content_id": score.content_id,
            "vertical_code": score.vertical_code,
            "relevance": score.relevance,
            "dim_scores": score.dim_scores,
            "vertical_tags": score.vertical_tags,
            "quality_score": score.quality_score,
            "reviewed": score.reviewed,
            "review_note": score.review_note,
            "rubric_version": score.rubric_version,
            "model": score.model,
        }
        with self.engine.begin() as conn:
            conn.execute(
                delete(models.content_vertical_scores).where(
                    models.content_vertical_scores.c.content_id == score.content_id,
                    models.content_vertical_scores.c.vertical_code == score.vertical_code,
                )
            )
            conn.execute(insert(models.content_vertical_scores).values(**values))
        self.cost_units[score.content_id] = self.cost_units.get(score.content_id, 0) + 1

    def persist_translation(self, translation: Translation) -> None:
        fields: dict[str, object] = dict(translation.fields)
        if translation.terms:
            fields["_terms"] = dict(translation.terms)
        values = {
            "content_id": translation.content_id,
            "lang": translation.lang,
            "fields": fields,
            "model": translation.model,
        }
        with self.engine.begin() as conn:
            conn.execute(
                delete(models.content_translations).where(
                    models.content_translations.c.content_id == translation.content_id,
                    models.content_translations.c.lang == translation.lang,
                )
            )
            conn.execute(insert(models.content_translations).values(**values))
        self.cost_units[translation.content_id] = self.cost_units.get(translation.content_id, 0) + 1

    def enqueue_review(self, content_id: int, vertical_code: str, reason: str) -> ReviewItem:
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(models.review_queue).where(
                    models.review_queue.c.content_id == content_id,
                    models.review_queue.c.vertical_code == vertical_code,
                    models.review_queue.c.status == "pending",
                )
            ).mappings().first()
            self.set_status(content_id, "WAIT_REVIEW")
            if existing is not None:
                return self._review_from_row(existing)
            result = conn.execute(
                insert(models.review_queue)
                .values(
                    content_id=content_id,
                    vertical_code=vertical_code,
                    reason=reason,
                    status="pending",
                )
                .returning(models.review_queue)
            )
            return self._review_from_row(result.mappings().one())

    def mark_completed(self, content_id: int) -> None:
        self.set_status(content_id, "COMPLETED")
        event = OutboxEvent(type="content.completed", payload={"content_id": content_id})
        if not any(
            item.type == event.type and item.payload == event.payload for item in self._outbox
        ):
            self._outbox.append(event)

    def decide_review(
        self,
        review_id: int,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        note: str,
    ) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(models.review_queue).where(models.review_queue.c.id == review_id)
            ).mappings().one()
            conn.execute(
                update(models.review_queue)
                .where(models.review_queue.c.id == review_id)
                .values(status=decision, reviewer=reviewer, decided_at=datetime.now(UTC))
            )
            conn.execute(
                update(models.content_vertical_scores)
                .where(
                    models.content_vertical_scores.c.content_id == row["content_id"],
                    models.content_vertical_scores.c.vertical_code == row["vertical_code"],
                )
                .values(reviewed=True, review_note=note)
            )
        if decision == "approved":
            self.mark_completed(int(row["content_id"]))
        else:
            self.set_status(int(row["content_id"]), "CANCELLED")

    def completed_scores(self, vertical: str) -> list[VerticalScore]:
        query: Select[tuple[object, ...]] = (
            select(models.content_vertical_scores)
            .where(
                models.content_vertical_scores.c.vertical_code == vertical,
            )
        )
        with self.engine.begin() as conn:
            return [
                self._score_from_row(row)
                for row in conn.execute(query).mappings()
                if self.statuses.get(int(row["content_id"])) == "COMPLETED"
            ]

    def reprocess_needed(self, vertical: str, rubric_version: str) -> list[int]:
        with self.engine.begin() as conn:
            return [
                int(value)
                for value in conn.execute(
                    select(models.content_vertical_scores.c.content_id).where(
                        models.content_vertical_scores.c.vertical_code == vertical,
                        models.content_vertical_scores.c.rubric_version != rubric_version,
                    )
                ).scalars()
            ]

    def outbox(self) -> list[OutboxEvent]:
        return list(self._outbox)

    def translation(self, content_id: int, lang: str) -> Translation | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(models.content_translations).where(
                    models.content_translations.c.content_id == content_id,
                    models.content_translations.c.lang == lang,
                )
            ).mappings().first()
        if row is None:
            return None
        return Translation(
            content_id=int(row["content_id"]),
            lang=cast(Literal["zh", "en"], row["lang"]),
            fields={
                str(k): str(v)
                for k, v in dict(row["fields"]).items()
                if str(k) != "_terms"
            },
            model=str(row["model"]),
            terms={str(k): str(v) for k, v in dict(row["fields"]).get("_terms", {}).items()},
        )

    def as_content_ref(self, score: VerticalScore, rank_score: float) -> ContentRef:
        return ContentRef(
            content_id=score.content_id,
            vertical_code=score.vertical_code,
            quality_score=score.quality_score,
            relevance=score.relevance,
            tags=score.vertical_tags,
            rank_score=rank_score,
        )

    def _score_from_row(self, row: RowMapping) -> VerticalScore:
        return VerticalScore(
            content_id=int(row["content_id"]),
            vertical_code=str(row["vertical_code"]),
            relevance=int(row["relevance"]),
            dim_scores={str(k): int(v) for k, v in dict(row["dim_scores"]).items()},
            vertical_tags=[str(tag) for tag in row["vertical_tags"]],
            quality_score=int(row["quality_score"]),
            reviewed=bool(row["reviewed"]),
            review_note=cast(str | None, row["review_note"]),
            rubric_version=str(row["rubric_version"]),
            model=str(row["model"]),
        )

    def _review_from_row(self, row: RowMapping) -> ReviewItem:
        return ReviewItem(
            id=int(row["id"]),
            content_id=int(row["content_id"]),
            vertical_code=str(row["vertical_code"]),
            reason=str(row["reason"]),
            status=cast(Literal["pending", "approved", "rejected"], row["status"]),
            reviewer=cast(str | None, row["reviewer"]),
            decided_at=row["decided_at"],
        )
