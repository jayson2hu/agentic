import pytest
from judgment_graph.workers.scoring.enqueue import ScoreEnqueuer, enqueue_content_analyzed


class FakeArqQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> object:
        self.jobs.append((function, args, kwargs))
        return {"job_id": f"{function}:{args[0]}"}


@pytest.mark.asyncio
async def test_score_enqueuer_maps_content_analyzed_to_score_once() -> None:
    queue = FakeArqQueue()
    enqueuer = ScoreEnqueuer(queue)

    assert await enqueuer.consume("content.analyzed", {"content_id": 1001}) is True
    assert await enqueuer.consume("content.analyzed", {"content_id": 1001}) is False
    assert queue.jobs == [("score", (1001,), {})]


@pytest.mark.asyncio
async def test_enqueue_content_analyzed_helper() -> None:
    queue = FakeArqQueue()

    assert await enqueue_content_analyzed(queue, "content.analyzed", {"content_id": 1002}) is True
    assert queue.jobs == [("score", (1002,), {})]

