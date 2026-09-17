from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from judgment_graph.contracts import DIMENSIONS, VerticalScore
from judgment_graph.http_api import ContentReadService, create_app
from judgment_graph.persist.repository import InMemoryJudgmentRepository
from judgment_graph.review.queue import route_review
from judgment_graph.scripts.prepare_preview import prepare
from test_http_api import seed_l1, seed_l2
from test_real_preview import analysis


def test_low_rule_priority_cannot_bypass_review_by_falling_below_old_band() -> None:
    repository = InMemoryJudgmentRepository()
    repository.set_status(1, "WAIT_SCORE")
    score = VerticalScore(
        content_id=1, vertical_code="ai-coding", relevance=40,
        dim_scores={dimension: 40 for dimension in DIMENSIONS},
        vertical_tags=[], quality_score=40, reviewed=False,
        rubric_version="v1", model="heuristic-v1",
    )
    repository.persist_score(score)
    route_review(score, analysis(), repository)
    assert repository.get_status(1) == "WAIT_REVIEW"


def test_preview_refuses_existing_simulated_results(tmp_path) -> None:
    l1, l2 = tmp_path / "l1.db", tmp_path / "l2.db"
    seed_l1(f"sqlite:///{l1}")
    seed_l2(f"sqlite:///{l2}")
    with pytest.raises(ValueError, match="separate L2 preview"):
        prepare(l1, l2)


def test_http_reads_unknown_date_as_null(tmp_path) -> None:
    provider = seed_l1(f"sqlite:///{tmp_path / 'l1.db'}")
    repository = seed_l2(f"sqlite:///{tmp_path / 'l2.db'}")
    document = replace(provider.get_document(1), published_at=None)

    class DocumentProvider:
        def get_document(self, _content_id):
            return document

    service = ContentReadService(repository, DocumentProvider())
    api = TestClient(create_app(service))
    assert api.get("/content/1").json()["published_at"] is None
    assert api.get("/content").json()["items"][0]["published_at"] is None
    assert api.get("/content", params={"q": "x" * 201}).status_code == 422


def test_missing_l1_schema_is_configuration_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("L2_DATABASE_URL", f"sqlite:///{tmp_path / 'l2.db'}")
    monkeypatch.setenv("L2_L1_DATABASE_URL", f"sqlite:///{tmp_path / 'empty-l1.db'}")
    result = TestClient(create_app()).get("/content")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "configuration_error"
