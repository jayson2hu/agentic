import asyncio

import pytest
from judgment_graph.workers.scoring.enqueue import ScoreEnqueuer, enqueue_content_analyzed


class FakeArqQueue:
    """Model Arq's shared job/result retention and None-on-duplicate contract."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.retained_job_ids: set[str] = set()
        self.fail_next = False

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0)
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("Redis connection interrupted")
        job_id = str(kwargs.get("_job_id", len(self.jobs)))
        if job_id in self.retained_job_ids:
            return None
        self.retained_job_ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return {"job_id": job_id}


@pytest.mark.asyncio
async def test_score_enqueuer_maps_content_analyzed_to_score_once() -> None:
    queue = FakeArqQueue()
    enqueuer = ScoreEnqueuer(queue)

    assert await enqueuer.consume("content.analyzed", {"content_id": 1001}) is True
    assert await enqueuer.consume("content.analyzed", {"content_id": 1001}) is False
    assert queue.jobs == [("score", (1001,), {"_job_id": "score:1001"})]


@pytest.mark.asyncio
async def test_enqueue_content_analyzed_helper() -> None:
    queue = FakeArqQueue()

    assert await enqueue_content_analyzed(queue, "content.analyzed", {"content_id": 1002})
    assert queue.jobs == [("score", (1002,), {"_job_id": "score:1002"})]


@pytest.mark.asyncio
async def test_enqueue_can_retry_after_redis_failure() -> None:
    queue = FakeArqQueue()
    queue.fail_next = True
    enqueuer = ScoreEnqueuer(queue)

    with pytest.raises(ConnectionError, match="Redis connection interrupted"):
        await enqueuer.consume("content.analyzed", {"content_id": 1001})
    assert await enqueuer.consume("content.analyzed", {"content_id": 1001}) is True
    assert len(queue.jobs) == 1


@pytest.mark.asyncio
async def test_shared_queue_deduplicates_separate_instances_and_concurrent_helpers() -> None:
    queue = FakeArqQueue()
    results = await asyncio.gather(
        ScoreEnqueuer(queue).consume("content.analyzed", {"content_id": 1001}),
        ScoreEnqueuer(queue).consume("content.analyzed", {"content_id": 1001}),
        enqueue_content_analyzed(queue, "content.analyzed", {"content_id": 1001}),
        enqueue_content_analyzed(queue, "content.analyzed", {"content_id": 1001}),
    )

    assert results.count(True) == 1
    assert results.count(False) == 3
    assert len(queue.jobs) == 1
    assert await enqueue_content_analyzed(queue, "content.analyzed", {"content_id": 1002})
    assert len(queue.jobs) == 2


@pytest.mark.asyncio
async def test_enqueue_is_allowed_after_arq_retention_expires() -> None:
    queue = FakeArqQueue()
    enqueuer = ScoreEnqueuer(queue)

    assert await enqueuer.consume("content.analyzed", {"content_id": 1001})
    queue.retained_job_ids.clear()
    assert await enqueuer.consume("content.analyzed", {"content_id": 1001})
    assert len(queue.jobs) == 2


@pytest.mark.asyncio
async def test_unsupported_event_does_not_enqueue() -> None:
    queue = FakeArqQueue()

    with pytest.raises(ValueError, match="unsupported event"):
        await ScoreEnqueuer(queue).consume("content.completed", {"content_id": 1001})
    assert queue.jobs == []
