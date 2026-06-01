from dataclasses import replace

import pytest

from judgment_graph.contracts import VerticalScore
from judgment_graph.persist.repository import InMemoryJudgmentRepository


def score(content_id: int = 1, rubric_version: str = "aic-v1") -> VerticalScore:
    return VerticalScore(
        content_id=content_id,
        vertical_code="ai-coding",
        relevance=80,
        dim_scores={
            "topic": 80,
            "content": 80,
            "depth": 80,
            "practical": 80,
            "novelty": 80,
            "expression": 80,
        },
        vertical_tags=["agent-engineering"],
        quality_score=80,
        reviewed=False,
        rubric_version=rubric_version,
        model="fake-l2-model",
    )


def test_score_schema_rejects_bad_dimensions() -> None:
    with pytest.raises(ValueError):
        replace(score(), dim_scores={"topic": 101})


def test_upsert_and_reprocess_detection() -> None:
    repository = InMemoryJudgmentRepository()
    repository.persist_score(score(content_id=1, rubric_version="old"))
    repository.persist_score(score(content_id=1, rubric_version="old"))
    assert len(repository.content_vertical_scores) == 1
    assert repository.reprocess_needed("ai-coding", "aic-v1") == [1]

