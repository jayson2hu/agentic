"""Relay durable L2 completion events to Redis and consume acknowledgements."""
from __future__ import annotations

import argparse
import os
import time
from datetime import timedelta
from typing import cast

from redis import Redis
from sqlalchemy import create_engine

from judgment_graph.persist.delivery import (
    CompletionOutboxRelay,
    CompletionOutboxStore,
    RedisCompletionTransport,
    RedisLike,
    RelayStats,
)


def relay_once_to_redis(
    database_url: str,
    redis_url: str,
    *,
    queue_name: str = "codepick:l2:events",
    ack_queue_name: str = "codepick:l2:events:acks",
    max_attempts: int = 5,
    ack_timeout_sec: float = 300,
    batch_size: int = 100,
) -> RelayStats:
    engine = create_engine(database_url)
    try:
        client = cast(RedisLike, Redis.from_url(redis_url, decode_responses=True))
        transport = RedisCompletionTransport(
            client,
            queue_name=queue_name,
            ack_queue_name=ack_queue_name,
        )
        return CompletionOutboxRelay(
            CompletionOutboxStore(engine),
            transport,
            transport,
            max_attempts=max_attempts,
            ack_timeout=timedelta(seconds=ack_timeout_sec),
            batch_size=batch_size,
        ).relay_once()
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("L2_DATABASE_URL"))
    parser.add_argument(
        "--redis-url",
        default=os.getenv("L2_REDIS_URL", "redis://127.0.0.1:6379/0"),
    )
    parser.add_argument(
        "--queue-name",
        default=os.getenv("L2_COMPLETION_QUEUE", "codepick:l2:events"),
    )
    parser.add_argument(
        "--ack-queue-name",
        default=os.getenv("L2_COMPLETION_ACK_QUEUE", "codepick:l2:events:acks"),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.getenv("L2_COMPLETION_MAX_ATTEMPTS", "5")),
    )
    parser.add_argument(
        "--ack-timeout-sec",
        type=float,
        default=float(os.getenv("L2_COMPLETION_ACK_TIMEOUT_SEC", "300")),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("L2_COMPLETION_BATCH_SIZE", "100")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("L2_DATABASE_URL or --database-url is required")
    if args.max_attempts <= 0:
        raise SystemExit("--max-attempts must be positive")
    if args.ack_timeout_sec <= 0:
        raise SystemExit("--ack-timeout-sec must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.poll_interval <= 0:
        raise SystemExit("--poll-interval must be positive")
    while True:
        stats = relay_once_to_redis(
            args.database_url,
            args.redis_url,
            queue_name=args.queue_name,
            ack_queue_name=args.ack_queue_name,
            max_attempts=args.max_attempts,
            ack_timeout_sec=args.ack_timeout_sec,
            batch_size=args.batch_size,
        )
        print(
            "L2 COMPLETION RELAY: "
            f"acked={stats.acknowledged} claimed={stats.claimed} "
            f"published={stats.published} failed={stats.failed} "
            f"dead={stats.dead_lettered}"
        )
        if args.once:
            return
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
