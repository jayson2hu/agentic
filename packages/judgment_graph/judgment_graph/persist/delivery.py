"""Reliable at-least-once delivery for durable L2 completion events."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import Engine, and_, or_, select, update
from sqlalchemy.engine import RowMapping

from judgment_graph.persist import models


def completion_event_id(event_type: str, content_id: int, source_revision: int) -> str:
    return f"{event_type}:{content_id}-r{source_revision}"


@dataclass(frozen=True)
class CompletionDelivery:
    id: int
    event_id: str
    event_type: str
    content_id: int
    source_run_id: str | None
    source_revision: int
    payload: dict[str, Any]
    attempt_count: int
    sent_at: datetime | None
    acked_at: datetime | None
    dead_lettered_at: datetime | None
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True)
class RelayStats:
    acknowledged: int = 0
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0


class CompletionPublisher(Protocol):
    def __call__(self, event: CompletionDelivery) -> None: ...


class AckSource(Protocol):
    def drain(self, limit: int) -> list[str]: ...


class CompletionOutboxStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def claim_pending(
        self,
        *,
        now: datetime,
        ack_timeout: timedelta,
        max_attempts: int,
        limit: int,
    ) -> list[CompletionDelivery]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if limit < 1:
            raise ValueError("limit must be positive")
        table = models.judgment_outbox
        cutoff = now - ack_timeout
        eligible = and_(
            table.c.acked_at.is_(None),
            table.c.dead_lettered_at.is_(None),
            table.c.attempt_count < max_attempts,
            or_(table.c.sent_at.is_(None), table.c.sent_at <= cutoff),
        )
        claimed: list[CompletionDelivery] = []
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(table)
                .where(eligible)
                .order_by(table.c.created_at, table.c.id)
                .limit(limit)
            ).mappings().all()
            for row in rows:
                result = conn.execute(
                    update(table)
                    .where(table.c.id == row["id"], eligible)
                    .values(
                        attempt_count=table.c.attempt_count + 1,
                        sent_at=now,
                        last_error=None,
                    )
                )
                if result.rowcount == 1:
                    values = dict(row)
                    values["attempt_count"] = int(row["attempt_count"]) + 1
                    values["sent_at"] = now
                    values["last_error"] = None
                    claimed.append(_delivery_from_row(values))
        return claimed

    def acknowledge(self, event_id: str, *, now: datetime) -> bool:
        table = models.judgment_outbox
        with self.engine.begin() as conn:
            result = conn.execute(
                update(table)
                .where(
                    table.c.event_id == event_id,
                    table.c.acked_at.is_(None),
                    table.c.dead_lettered_at.is_(None),
                )
                .values(acked_at=now)
            )
        return result.rowcount == 1

    def record_failure(
        self,
        event_id: str,
        error: str,
        *,
        now: datetime,
        max_attempts: int,
    ) -> bool:
        table = models.judgment_outbox
        with self.engine.begin() as conn:
            row = conn.execute(
                select(table.c.attempt_count, table.c.acked_at, table.c.dead_lettered_at)
                .where(table.c.event_id == event_id)
                .with_for_update()
            ).mappings().one()
            if row["acked_at"] is not None or row["dead_lettered_at"] is not None:
                return False
            exhausted = int(row["attempt_count"]) >= max_attempts
            values: dict[str, object] = {"last_error": error[:2000]}
            if exhausted:
                values["dead_lettered_at"] = now
            else:
                values["sent_at"] = None
            conn.execute(
                update(table).where(table.c.event_id == event_id).values(**values)
            )
        return exhausted

    def dead_letter_expired(
        self,
        *,
        now: datetime,
        ack_timeout: timedelta,
        max_attempts: int,
    ) -> int:
        table = models.judgment_outbox
        cutoff = now - ack_timeout
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(table.c.event_id, table.c.attempt_count)
                .where(
                    table.c.acked_at.is_(None),
                    table.c.dead_lettered_at.is_(None),
                    table.c.sent_at.is_not(None),
                    table.c.sent_at <= cutoff,
                    table.c.attempt_count >= max_attempts,
                )
                .with_for_update()
            ).mappings().all()
            for row in rows:
                conn.execute(
                    update(table)
                    .where(
                        table.c.event_id == row["event_id"],
                        table.c.acked_at.is_(None),
                        table.c.dead_lettered_at.is_(None),
                    )
                    .values(
                        dead_lettered_at=now,
                        last_error=(
                            f"ack timeout after {int(row['attempt_count'])} attempts"
                        ),
                    )
                )
        return len(rows)

    def get(self, event_id: str) -> CompletionDelivery | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(models.judgment_outbox).where(
                    models.judgment_outbox.c.event_id == event_id
                )
            ).mappings().first()
        return _delivery_from_row(row) if row is not None else None


class CompletionOutboxRelay:
    def __init__(
        self,
        store: CompletionOutboxStore,
        publisher: CompletionPublisher,
        ack_source: AckSource,
        *,
        max_attempts: int = 5,
        ack_timeout: timedelta = timedelta(minutes=5),
        batch_size: int = 100,
    ) -> None:
        self.store = store
        self.publisher = publisher
        self.ack_source = ack_source
        self.max_attempts = max_attempts
        self.ack_timeout = ack_timeout
        self.batch_size = batch_size

    def relay_once(self, *, now: datetime | None = None) -> RelayStats:
        current = now or datetime.now(UTC)
        acknowledged = sum(
            self.store.acknowledge(event_id, now=current)
            for event_id in self.ack_source.drain(self.batch_size)
        )
        dead_lettered = self.store.dead_letter_expired(
            now=current,
            ack_timeout=self.ack_timeout,
            max_attempts=self.max_attempts,
        )
        events = self.store.claim_pending(
            now=current,
            ack_timeout=self.ack_timeout,
            max_attempts=self.max_attempts,
            limit=self.batch_size,
        )
        published = 0
        failed = 0
        for event in events:
            try:
                self.publisher(event)
            except Exception as exc:  # noqa: BLE001 - transport failures are retried
                failed += 1
                dead_lettered += self.store.record_failure(
                    event.event_id,
                    f"{type(exc).__name__}: {exc}",
                    now=current,
                    max_attempts=self.max_attempts,
                )
            else:
                published += 1
        return RelayStats(
            acknowledged=acknowledged,
            claimed=len(events),
            published=published,
            failed=failed,
            dead_lettered=dead_lettered,
        )



class RedisLike(Protocol):
    def rpush(self, name: str, value: str) -> object: ...

    def lpop(self, name: str) -> object: ...


class RedisCompletionTransport:
    def __init__(
        self,
        redis_client: RedisLike,
        *,
        queue_name: str = "codepick:l2:events",
        ack_queue_name: str = "codepick:l2:events:acks",
    ) -> None:
        self.redis = redis_client
        self.queue_name = queue_name
        self.ack_queue_name = ack_queue_name

    def __call__(self, event: CompletionDelivery) -> None:
        envelope = {
            "topic": event.event_type,
            "payload": event.payload,
            "idempotency_key": event.event_id,
        }
        serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        self.redis.rpush(self.queue_name, serialized)

    def drain(self, limit: int) -> list[str]:
        event_ids: list[str] = []
        for _ in range(limit):
            raw = self.redis.lpop(self.ack_queue_name)
            if raw is None:
                break
            if isinstance(raw, bytes):
                event_ids.append(raw.decode("utf-8"))
            elif isinstance(raw, str):
                event_ids.append(raw)
            else:
                raise TypeError("completion ack must be a string event_id")
        return event_ids


def _delivery_from_row(
    row: RowMapping | Mapping[str, Any],
) -> CompletionDelivery:
    values = cast(Mapping[str, Any], row)
    return CompletionDelivery(
        id=int(values["id"]),
        event_id=str(values["event_id"]),
        event_type=str(values["event_type"]),
        content_id=int(values["content_id"]),
        source_run_id=(
            str(values["source_run_id"]) if values["source_run_id"] is not None else None
        ),
        source_revision=int(values["source_revision"]),
        payload=dict(values["payload"]),
        attempt_count=int(values["attempt_count"]),
        sent_at=_as_utc(values["sent_at"]),
        acked_at=_as_utc(values["acked_at"]),
        dead_lettered_at=_as_utc(values["dead_lettered_at"]),
        last_error=str(values["last_error"]) if values["last_error"] is not None else None,
        created_at=_required_utc(values["created_at"]),
    )


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("expected datetime delivery timestamp")
    return _required_utc(value)


def _required_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected datetime delivery timestamp")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
