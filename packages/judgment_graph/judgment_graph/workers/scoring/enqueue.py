from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ArqQueue(Protocol):
    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object: ...


@dataclass
class ScoreEnqueuer:
    queue: ArqQueue
    _seen: set[int] = field(default_factory=set)

    async def consume(self, event_type: str, payload: dict[str, int]) -> bool:
        if event_type != "content.analyzed":
            raise ValueError(f"unsupported event: {event_type}")
        content_id = int(payload["content_id"])
        if content_id in self._seen:
            return False
        self._seen.add(content_id)
        await self.queue.enqueue_job("score", content_id)
        return True


async def enqueue_content_analyzed(
    queue: ArqQueue, event_type: str, payload: dict[str, int]
) -> bool:
    return await ScoreEnqueuer(queue).consume(event_type, payload)

