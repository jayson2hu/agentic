from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import ClassVar

from arq.connections import RedisSettings

from judgment_graph.events.consume import EventConsumer
from judgment_graph.input.factory import create_analysis_provider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.runtime import create_judgment_repository

repository = create_judgment_repository()
consumer = EventConsumer(create_analysis_provider(), FileLensLoader(), FakeLLM(), repository)


async def score(
    ctx: dict[str, object],
    content_id: int,
    run_id: str | None = None,
    revision: int | None = None,
) -> None:
    del ctx
    payload: dict[str, object] = {"content_id": content_id}
    if run_id is not None:
        payload["run_id"] = run_id
    if revision is not None:
        payload["revision"] = revision
    consumer.consume("content.analyzed", payload)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Awaitable[None]]]] = [score]
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(
        os.getenv("L2_REDIS_URL", "redis://127.0.0.1:6379/0")
    )
