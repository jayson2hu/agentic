from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

from judgment_graph.events.consume import EventConsumer
from judgment_graph.input.factory import create_analysis_provider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.repository import InMemoryJudgmentRepository

repository = InMemoryJudgmentRepository()
consumer = EventConsumer(create_analysis_provider(), FileLensLoader(), FakeLLM(), repository)


async def score(ctx: dict[str, object], content_id: int) -> None:
    del ctx
    consumer.consume("content.analyzed", {"content_id": content_id})


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Awaitable[None]]]] = [score]
