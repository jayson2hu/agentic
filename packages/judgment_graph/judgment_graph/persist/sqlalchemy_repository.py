from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import Connection, Engine, create_engine, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from judgment_graph.contracts import (
    ContentRef,
    ContentStatus,
    Lens,
    OutboxEvent,
    ReviewItem,
    Translation,
    VerticalScore,
)
from judgment_graph.persist import models
from judgment_graph.persist.delivery import completion_event_id


def create_sqlalchemy_engine(url: str) -> Engine:
    return create_engine(url, future=True)


class ConcurrentJudgmentUpdateError(RuntimeError):
    """The durable state changed after it was read; retry from a fresh transaction."""


class SqlAlchemyJudgmentRepository:
    """SQLAlchemy implementation for L2-owned tables and outbox state."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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

    @property
    def statuses(self) -> dict[int, ContentStatus]:
        with self.engine.connect() as conn:
            return {
                int(row.content_id): cast(ContentStatus, row.status)
                for row in conn.execute(select(models.content_judgment_state))
                if row.status is not None
            }

    @property
    def cost_units(self) -> dict[int, int]:
        with self.engine.connect() as conn:
            return {
                int(row.content_id): int(row.cost_units)
                for row in conn.execute(select(models.content_judgment_state))
            }

    def begin_version(
        self, content_id: int, source_run_id: str, source_revision: int
    ) -> bool:
        if not source_run_id.strip() or source_revision < 1:
            raise ValueError("versioned processing requires a run_id and positive revision")
        table = models.content_judgment_state
        with self.engine.begin() as conn:
            row = conn.execute(
                select(table).where(table.c.content_id == content_id).with_for_update()
            ).mappings().first()
            current_revision = int(row["source_revision"]) if row is not None else 0
            if source_revision < current_revision:
                return False
            if source_revision == current_revision:
                if row is not None and row["source_run_id"] != source_run_id:
                    raise ValueError("source revision is already associated with another run")
                return False
            values = {
                "status": "WAIT_SCORE",
                "source_run_id": source_run_id,
                "source_revision": source_revision,
                "cost_units": 0,
                "updated_at": datetime.now(UTC),
            }
            if row is None:
                try:
                    conn.execute(insert(table).values(content_id=content_id, **values))
                except IntegrityError as exc:
                    raise ConcurrentJudgmentUpdateError(
                        "judgment state was created concurrently"
                    ) from exc
            else:
                result = conn.execute(
                    update(table)
                    .where(
                        table.c.content_id == content_id,
                        table.c.source_revision == current_revision,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise ConcurrentJudgmentUpdateError(
                        "a newer source revision was accepted concurrently"
                    )
            conn.execute(
                delete(models.review_queue).where(
                    models.review_queue.c.content_id == content_id,
                    models.review_queue.c.status == "pending",
                )
            )
            return True

    def set_status(
        self,
        content_id: int,
        status: ContentStatus,
        *,
        source_revision: int | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            self._set_status(
                conn, content_id, status, source_revision=source_revision
            )

    def _set_status(
        self,
        conn: Connection,
        content_id: int,
        status: ContentStatus,
        *,
        source_revision: int | None = None,
    ) -> None:
        table = models.content_judgment_state
        row = conn.execute(
            select(table).where(table.c.content_id == content_id).with_for_update()
        ).mappings().first()
        current = row["status"] if row is not None else None
        if (
            source_revision is not None
            and (row is None or int(row["source_revision"]) != source_revision)
        ):
            raise ConcurrentJudgmentUpdateError(
                "a newer source revision is already active"
            )
        legal = {
            None: {"WAIT_SCORE", "CANCELLED"},
            "WAIT_SCORE": {"WAIT_REVIEW", "COMPLETED", "CANCELLED", "WAIT_SCORE"},
            "WAIT_REVIEW": {"COMPLETED", "CANCELLED", "WAIT_REVIEW"},
            "COMPLETED": {"COMPLETED"},
            "CANCELLED": {"CANCELLED"},
        }
        if current not in legal or status not in legal[current]:
            raise ValueError(f"illegal status transition: {current} -> {status}")
        values = {"status": status, "updated_at": datetime.now(UTC)}
        if row is None:
            try:
                conn.execute(insert(table).values(content_id=content_id, cost_units=0, **values))
            except IntegrityError as exc:
                raise ConcurrentJudgmentUpdateError("judgment state was created concurrently") from exc
        else:
            expected = table.c.status.is_(None) if current is None else table.c.status == current
            conditions = [table.c.content_id == content_id, expected]
            if source_revision is not None:
                conditions.append(table.c.source_revision == source_revision)
            result = conn.execute(update(table).where(*conditions).values(**values))
            if result.rowcount != 1:
                raise ConcurrentJudgmentUpdateError("judgment state changed concurrently")

    def _guard_product_write(
        self,
        conn: Connection,
        content_id: int,
        source_revision: int | None,
    ) -> None:
        """Lock a writable lifecycle row before changing any product or its cost."""
        table = models.content_judgment_state
        conditions = [
            table.c.content_id == content_id,
            (table.c.status == "WAIT_SCORE") | table.c.status.is_(None),
        ]
        if source_revision is not None:
            conditions.append(table.c.source_revision == source_revision)
        result = conn.execute(
            update(table).where(*conditions).values(updated_at=datetime.now(UTC))
        )
        if result.rowcount == 1:
            return
        row = conn.execute(
            select(table.c.status).where(table.c.content_id == content_id)
        ).first()
        if row is not None:
            if source_revision is not None:
                raise ConcurrentJudgmentUpdateError(
                    "a newer source revision is already active"
                )
            raise ConcurrentJudgmentUpdateError(f"content products are sealed in {row.status}")
        # Direct development seeds may create products before lifecycle initialization.
        try:
            conn.execute(insert(table).values(content_id=content_id, status=None, cost_units=0))
        except IntegrityError as exc:
            raise ConcurrentJudgmentUpdateError("judgment state was created concurrently") from exc

    def _add_cost(
        self, conn: Connection, content_id: int, source_revision: int | None
    ) -> None:
        table = models.content_judgment_state
        conditions = [table.c.content_id == content_id]
        if source_revision is not None:
            conditions.append(table.c.source_revision == source_revision)
        result = conn.execute(update(table).where(*conditions).values(
            cost_units=table.c.cost_units + 1,
            updated_at=datetime.now(UTC),
        ))
        if result.rowcount == 0:
            if source_revision is not None:
                raise ConcurrentJudgmentUpdateError(
                    "a newer source revision is already active"
                )
            conn.execute(insert(table).values(content_id=content_id, cost_units=1))

    def get_status(self, content_id: int) -> ContentStatus | None:
        with self.engine.connect() as conn:
            value = conn.execute(
                select(models.content_judgment_state.c.status)
                .where(models.content_judgment_state.c.content_id == content_id)
            ).scalar_one_or_none()
        return cast(ContentStatus | None, value)

    def persist_score(
        self, score: VerticalScore, *, source_revision: int | None = None
    ) -> None:
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
            self._guard_product_write(conn, score.content_id, source_revision)
            conn.execute(
                delete(models.content_vertical_scores).where(
                    models.content_vertical_scores.c.content_id == score.content_id,
                    models.content_vertical_scores.c.vertical_code == score.vertical_code,
                )
            )
            conn.execute(insert(models.content_vertical_scores).values(**values))
            self._add_cost(conn, score.content_id, source_revision)

    def persist_translation(
        self, translation: Translation, *, source_revision: int | None = None
    ) -> None:
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
            self._guard_product_write(
                conn, translation.content_id, source_revision
            )
            conn.execute(
                delete(models.content_translations).where(
                    models.content_translations.c.content_id == translation.content_id,
                    models.content_translations.c.lang == translation.lang,
                )
            )
            conn.execute(insert(models.content_translations).values(**values))
            self._add_cost(conn, translation.content_id, source_revision)

    def enqueue_review(
        self,
        content_id: int,
        vertical_code: str,
        reason: str,
        *,
        source_revision: int | None = None,
    ) -> ReviewItem:
        with self.engine.begin() as conn:
            # Acquire the state row's write lock before checking pending review rows.
            self._set_status(
                conn,
                content_id,
                "WAIT_REVIEW",
                source_revision=source_revision,
            )
            existing = conn.execute(
                select(models.review_queue).where(
                    models.review_queue.c.content_id == content_id,
                    models.review_queue.c.vertical_code == vertical_code,
                    models.review_queue.c.status == "pending",
                )
            ).mappings().first()
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

    def mark_completed(
        self, content_id: int, *, source_revision: int | None = None
    ) -> None:
        with self.engine.begin() as conn:
            self._mark_completed(
                conn, content_id, source_revision=source_revision
            )

    def _mark_completed(
        self,
        conn: Connection,
        content_id: int,
        *,
        source_revision: int | None = None,
    ) -> None:
        self._set_status(
            conn,
            content_id,
            "COMPLETED",
            source_revision=source_revision,
        )
        state = conn.execute(
            select(models.content_judgment_state).where(
                models.content_judgment_state.c.content_id == content_id
            )
        ).mappings().one()
        accepted_revision = int(state["source_revision"])
        accepted_run_id = cast(str | None, state["source_run_id"])
        payload: dict[str, object] = {"content_id": content_id}
        if accepted_revision:
            payload["source_revision"] = accepted_revision
        if accepted_run_id is not None:
            payload["source_run_id"] = accepted_run_id
        table = models.judgment_outbox
        values = {
            "event_id": completion_event_id(
                "content.completed", content_id, accepted_revision
            ),
            "event_type": "content.completed",
            "content_id": content_id,
            "source_run_id": accepted_run_id,
            "source_revision": accepted_revision,
            "payload": payload,
            "created_at": datetime.now(UTC),
        }
        if conn.dialect.name == "postgresql":
            conn.execute(pg_insert(table).values(**values).on_conflict_do_nothing(
                index_elements=["event_type", "content_id", "source_revision"]
            ))
        elif conn.dialect.name == "sqlite":
            conn.execute(sqlite_insert(table).values(**values).on_conflict_do_nothing(
                index_elements=["event_type", "content_id", "source_revision"]
            ))
        else:
            exists = conn.execute(select(table.c.id).where(
                table.c.event_type == "content.completed",
                table.c.content_id == content_id,
                table.c.source_revision == accepted_revision,
            )).first()
            if exists is None:
                conn.execute(insert(table).values(**values))

    def decide_review(
        self,
        review_id: int,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        note: str,
    ) -> None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("review decision must be approved or rejected")
        with self.engine.begin() as conn:
            row = conn.execute(
                select(models.review_queue).where(models.review_queue.c.id == review_id)
                .with_for_update()
            ).mappings().one()
            if row["status"] != "pending":
                if row["status"] == decision:
                    return
                raise ValueError("review decision is already final")
            result = conn.execute(
                update(models.review_queue)
                .where(models.review_queue.c.id == review_id, models.review_queue.c.status == "pending")
                .values(status=decision, reviewer=reviewer, decided_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise ConcurrentJudgmentUpdateError("review was decided concurrently")
            conn.execute(
                update(models.content_vertical_scores)
                .where(
                    models.content_vertical_scores.c.content_id == row["content_id"],
                    models.content_vertical_scores.c.vertical_code == row["vertical_code"],
                )
                .values(reviewed=True, review_note=note)
            )
            if decision == "approved":
                state = conn.execute(
                    select(models.content_judgment_state).where(
                        models.content_judgment_state.c.content_id == row["content_id"]
                    )
                ).mappings().one()
                self._mark_completed(
                    conn,
                    int(row["content_id"]),
                    source_revision=int(state["source_revision"]),
                )
            else:
                state = conn.execute(
                    select(models.content_judgment_state.c.source_revision).where(
                        models.content_judgment_state.c.content_id == row["content_id"]
                    )
                ).scalar_one()
                self._set_status(
                    conn,
                    int(row["content_id"]),
                    "CANCELLED",
                    source_revision=int(state),
                )

    def completed_scores(self, vertical: str | None = None) -> list[VerticalScore]:
        scores = models.content_vertical_scores
        state = models.content_judgment_state
        query = select(scores).join(state, scores.c.content_id == state.c.content_id).where(
            state.c.status == "COMPLETED"
        )
        if vertical is not None:
            query = query.where(scores.c.vertical_code == vertical)
        query = query.order_by(scores.c.content_id, scores.c.vertical_code)
        with self.engine.connect() as conn:
            return [self._score_from_row(row) for row in conn.execute(query).mappings()]

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
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(models.judgment_outbox).order_by(models.judgment_outbox.c.id)
            ).mappings().all()
        return [OutboxEvent(
            type=str(row["event_type"]), payload=dict(row["payload"]),
            created_at=row["created_at"].replace(tzinfo=UTC)
            if row["created_at"].tzinfo is None else row["created_at"],
        ) for row in rows]

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
