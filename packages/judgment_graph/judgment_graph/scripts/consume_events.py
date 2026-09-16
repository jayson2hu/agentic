"""Continuously bridge durable L1 Redis events into the L2 Arq worker."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable
from typing import cast

from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from judgment_graph.workers.scoring.enqueue import ArqQueue, ScoreEnqueuer


def parse_envelope(raw: str | bytes) -> tuple[str, dict[str, object], str]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    value = json.loads(text)
    if not isinstance(value, dict):
        raise TypeError("event envelope must be an object")
    topic = value.get("topic")
    payload = value.get("payload")
    event_id = value.get("idempotency_key")
    if not isinstance(topic, str) or not topic:
        raise ValueError("event envelope topic is required")
    if not isinstance(payload, dict):
        raise TypeError("event envelope payload must be an object")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event envelope idempotency_key is required")
    return topic, {str(key): item for key, item in payload.items()}, event_id


async def recover_inflight(redis: Redis, queue: str, processing: str) -> int:
    recovered = 0
    while await cast(
        Awaitable[bytes | None], redis.rpoplpush(processing, queue)
    ) is not None:
        recovered += 1
    return recovered


async def consume(
    redis_url: str,
    *,
    queue: str,
    once: bool,
) -> int:
    redis = Redis.from_url(redis_url, decode_responses=False)
    arq = await create_pool(RedisSettings.from_dsn(redis_url))
    processing = f"{queue}:processing"
    dead = f"{queue}:dead"
    handled = 0
    try:
        await recover_inflight(redis, queue, processing)
        while True:
            raw = await cast(
                Awaitable[bytes | None],
                redis.blmove(
                    queue,
                    processing,
                    timeout=1,
                    src="LEFT",
                    dest="RIGHT",
                ),
            )
            if raw is None:
                if once:
                    return handled
                continue
            try:
                topic, payload, _event_id = parse_envelope(raw)
                await ScoreEnqueuer(cast(ArqQueue, arq)).consume(
                    topic, payload
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pipeline = redis.pipeline(transaction=True)
                pipeline.lpush(dead, raw)
                pipeline.lrem(processing, 1, raw)
                await pipeline.execute()
                if once:
                    return handled
                continue
            except Exception:
                pipeline = redis.pipeline(transaction=True)
                pipeline.lrem(processing, 1, raw)
                pipeline.rpush(queue, raw)
                await pipeline.execute()
                raise
            await redis.lrem(processing, 1, raw)
            handled += 1
            if once:
                return handled
    finally:
        await arq.close()
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--redis-url",
        default=os.getenv("L2_REDIS_URL", "redis://127.0.0.1:6379/0"),
    )
    parser.add_argument(
        "--queue",
        default=os.getenv("L2_EVENT_QUEUE", "codepick:l1:events"),
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    handled = asyncio.run(
        consume(args.redis_url, queue=args.queue, once=args.once)
    )
    print(f"L2 EVENT CONSUMER: handled={handled}")


if __name__ == "__main__":
    main()
