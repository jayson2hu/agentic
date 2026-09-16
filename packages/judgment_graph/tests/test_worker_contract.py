import importlib.util
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[1] / "judgment_graph" / "workers" / "scoring" / "worker.py"


def load_worker_module():
    spec = importlib.util.spec_from_file_location("l2_scoring_worker", WORKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_arq_worker_score_function_consumes_content_analyzed() -> None:
    worker = load_worker_module()
    await worker.score({}, 1001)

    assert worker.repository.statuses[1001] == "COMPLETED"
    assert (1001, "ai-coding") in worker.repository.content_vertical_scores
    assert worker.WorkerSettings.functions == [worker.score]
