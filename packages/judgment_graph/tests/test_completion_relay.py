from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from judgment_graph.persist.delivery import (
    CompletionDelivery,
    CompletionOutboxRelay,
    CompletionOutboxStore,
    RedisCompletionTransport,
)
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import create_engine


class AckQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    def drain(self, limit: int) -> list[str]:
        items = self.items[:limit]
        del self.items[:limit]
        return items


class RecordingPublisher:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.event_ids: list[str] = []

    def __call__(self, event: CompletionDelivery) -> None:
        self.event_ids.append(event.event_id)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("redis unavailable")


def completed_store(tmp_path: Path) -> tuple[CompletionOutboxStore, str]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'relay.db'}")
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.create_schema()
    repo.set_status(42, "WAIT_SCORE")
    repo.mark_completed(42)
    event_id = "content.completed:42-r0"
    assert CompletionOutboxStore(engine).get(event_id) is not None
    return CompletionOutboxStore(engine), event_id


def test_completion_event_is_sent_then_acknowledged_across_relay_runs(
    tmp_path: Path,
) -> None:
    store, event_id = completed_store(tmp_path)
    publisher = RecordingPublisher()
    acks = AckQueue()
    relay = CompletionOutboxRelay(store, publisher, acks)
    now = datetime(2026, 9, 16, tzinfo=UTC)

    first = relay.relay_once(now=now)
    assert (first.claimed, first.published, first.acknowledged) == (1, 1, 0)
    sent = store.get(event_id)
    assert sent is not None
    assert sent.attempt_count == 1
    assert sent.sent_at == now
    assert sent.acked_at is None

    assert relay.relay_once(now=now + timedelta(seconds=1)).claimed == 0
    acks.items.append(event_id)
    acknowledged = relay.relay_once(now=now + timedelta(seconds=2))
    assert acknowledged.acknowledged == 1
    assert store.get(event_id).acked_at == now + timedelta(seconds=2)  # type: ignore[union-attr]
    assert publisher.event_ids == [event_id]


def test_publish_failures_retry_with_stable_id_then_dead_letter(tmp_path: Path) -> None:
    store, event_id = completed_store(tmp_path)
    publisher = RecordingPublisher(failures=2)
    relay = CompletionOutboxRelay(
        store,
        publisher,
        AckQueue(),
        max_attempts=2,
        ack_timeout=timedelta(seconds=10),
    )
    now = datetime(2026, 9, 16, tzinfo=UTC)

    first = relay.relay_once(now=now)
    assert (first.failed, first.dead_lettered) == (1, 0)
    pending = store.get(event_id)
    assert pending is not None
    assert pending.sent_at is None
    assert pending.attempt_count == 1
    assert pending.last_error == "RuntimeError: redis unavailable"

    second = relay.relay_once(now=now + timedelta(seconds=1))
    assert (second.failed, second.dead_lettered) == (1, 1)
    dead = store.get(event_id)
    assert dead is not None
    assert dead.attempt_count == 2
    assert dead.dead_lettered_at == now + timedelta(seconds=1)
    assert relay.relay_once(now=now + timedelta(seconds=2)).claimed == 0
    assert publisher.event_ids == [event_id, event_id]


def test_unacknowledged_delivery_retries_then_expires_to_dead_letter(
    tmp_path: Path,
) -> None:
    store, event_id = completed_store(tmp_path)
    publisher = RecordingPublisher()
    relay = CompletionOutboxRelay(
        store,
        publisher,
        AckQueue(),
        max_attempts=2,
        ack_timeout=timedelta(seconds=10),
    )
    now = datetime(2026, 9, 16, tzinfo=UTC)

    assert relay.relay_once(now=now).published == 1
    assert relay.relay_once(now=now + timedelta(seconds=11)).published == 1
    expired = relay.relay_once(now=now + timedelta(seconds=22))
    assert (expired.claimed, expired.dead_lettered) == (0, 1)
    dead = store.get(event_id)
    assert dead is not None
    assert dead.last_error == "ack timeout after 2 attempts"
    assert publisher.event_ids == [event_id, event_id]


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.acks: list[object] = []

    def rpush(self, name: str, value: str) -> object:
        self.published.append((name, value))
        return len(self.published)

    def lpop(self, name: str) -> object:
        assert name == "acks"
        return self.acks.pop(0) if self.acks else None


def test_redis_transport_uses_stable_envelope_and_raw_ack_ids(tmp_path: Path) -> None:
    store, event_id = completed_store(tmp_path)
    event = store.claim_pending(
        now=datetime(2026, 9, 16, tzinfo=UTC),
        ack_timeout=timedelta(seconds=10),
        max_attempts=3,
        limit=1,
    )[0]
    redis = FakeRedis()
    redis.acks.extend([event_id.encode(), event_id])
    transport = RedisCompletionTransport(
        redis,
        queue_name="events",
        ack_queue_name="acks",
    )

    transport(event)
    transport(event)
    assert [queue for queue, _payload in redis.published] == ["events", "events"]
    envelope = json.loads(redis.published[0][1])
    assert envelope == {
        "idempotency_key": event_id,
        "payload": {"content_id": 42},
        "topic": "content.completed",
    }
    assert transport.drain(10) == [event_id, event_id]
