from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ArqQueue(Protocol):
    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object: ...


@dataclass
class ScoreEnqueuer:
    queue: ArqQueue

    async def consume(self, event_type: str, payload: dict[str, object]) -> bool:
        if event_type != "content.analyzed":
            raise ValueError(f"unsupported event: {event_type}")
        content_value = payload.get("content_id")
        if isinstance(content_value, bool) or not isinstance(
            content_value, (int, str)
        ):
            raise TypeError("content.analyzed content_id must be an integer")
        try:
            content_id = int(content_value)
        except ValueError as exc:
            raise ValueError(
                "content.analyzed content_id must be an integer"
            ) from exc
        run_id = payload.get("run_id")
        revision = payload.get("revision")
        if (run_id is None) != (revision is None):
            raise ValueError("content.analyzed requires run_id and revision together")
        args: tuple[object, ...] = (content_id,)
        job_id = f"score:{content_id}"
        if run_id is not None and revision is not None:
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("content.analyzed run_id must be a non-empty string")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise ValueError("content.analyzed revision must be a positive integer")
            args = (content_id, run_id, revision)
            job_id = f"score:{content_id}:{run_id}"
        # Arq atomically deduplicates across producers while its job/result is retained.
        # Exceptions remain retryable; no process-local success marker is stored.
        job = await self.queue.enqueue_job("score", *args, _job_id=job_id)
        return job is not None


async def enqueue_content_analyzed(
    queue: ArqQueue, event_type: str, payload: dict[str, object]
) -> bool:
    return await ScoreEnqueuer(queue).consume(event_type, payload)
