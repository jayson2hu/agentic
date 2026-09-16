from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ArqQueue(Protocol):
    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object: ...


@dataclass
class ScoreEnqueuer:
    queue: ArqQueue

    async def consume(self, event_type: str, payload: dict[str, int]) -> bool:
        if event_type != "content.analyzed":
            raise ValueError(f"unsupported event: {event_type}")
        content_id = int(payload["content_id"])
        # Arq atomically deduplicates across producers while its job/result is retained.
        # Exceptions remain retryable; no process-local success marker is stored.
        job = await self.queue.enqueue_job("score", content_id, _job_id=f"score:{content_id}")
        return job is not None


async def enqueue_content_analyzed(
    queue: ArqQueue, event_type: str, payload: dict[str, int]
) -> bool:
    return await ScoreEnqueuer(queue).consume(event_type, payload)
