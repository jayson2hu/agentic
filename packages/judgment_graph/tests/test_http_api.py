from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from judgment_graph.contracts import DIMENSIONS, Translation, VerticalScore
from judgment_graph.http_api import ContentDetail, ContentReadService, create_app
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
)


def seed_l1(url: str, *, include: bool = True) -> SqlAlchemyAnalysisProvider:
    engine = create_engine(url)
    metadata = MetaData()
    table = Table(
        "content_base_analysis",
        metadata,
        Column("schema_version", Integer, nullable=False),
        Column("content_id", String(255), primary_key=True),
        Column("input_snapshot", JSON, nullable=False),
        Column("analysis", JSON),
        Column("graph_version", String(255), nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("run_id", String(64), nullable=False),
        Column("status", String(32), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    if include:
        with engine.begin() as conn:
            conn.execute(
                insert(table).values(
                    schema_version=1,
                    content_id="1",
                    input_snapshot={
                        "content_id": "1",
                        "title": "M1 durable article",
                        "body": "The durable M1 article body.",
                        "source_url": "https://example.test/m1",
                        "published_at": "2026-09-12T08:00:00+00:00",
                        "metadata": {"source": {"name": "M1 Source"}, "lang": "en"},
                    },
                    analysis={
                        "content_id": "1",
                        "summary": "Persisted L1 summary",
                        "key_points": ["Durable L1", "Persistent L2"],
                        "quotes": ["Persistence survives restarts."],
                        "entities": ["CodePick"],
                        "base_tags": ["agent-engineering"],
                        "embedding": [0.1, 0.2],
                        "lang": "en",
                        "status": "COMPLETED",
                    },
                    graph_version="m1",
                    content_hash="hash",
                    run_id="run-1",
                    status="WAIT_SCORE",
                    updated_at=datetime(2026, 9, 12, 8, tzinfo=UTC),
                )
            )
    return SqlAlchemyAnalysisProvider(engine)


def seed_l2(url: str) -> SqlAlchemyJudgmentRepository:
    repository = SqlAlchemyJudgmentRepository(create_engine(url))
    repository.create_schema()
    repository.set_status(1, "WAIT_SCORE")
    repository.persist_score(
        VerticalScore(
            content_id=1,
            vertical_code="ai-coding",
            relevance=91,
            dim_scores={dimension: 84 for dimension in DIMENSIONS},
            vertical_tags=["agent-engineering"],
            quality_score=89,
            reviewed=False,
            rubric_version="aic-v1",
            model="fake-l2-model",
        )
    )
    repository.persist_translation(
        Translation(
            content_id=1,
            lang="zh",
            fields={
                "title": "ZH: M1 durable article",
                "summary": "ZH: Persisted summary",
                "key_points": "ZH: Durable L1 | ZH: Persistent L2",
            },
            model="fake-l2-model",
        )
    )
    repository.mark_completed(1)
    return repository


def client(root, *, include_l1: bool = True) -> TestClient:
    root.mkdir(parents=True, exist_ok=True)
    provider = seed_l1(f"sqlite+pysqlite:///{root / 'l1.db'}", include=include_l1)
    repository = seed_l2(f"sqlite+pysqlite:///{root / 'l2.db'}")
    return TestClient(create_app(ContentReadService(repository, provider)))


def test_http_content_list_detail_recommend_and_companion(tmp_path) -> None:
    api = client(tmp_path)
    page = api.get("/content", params={"vertical": "ai-coding", "sort": "score"})
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["title"] == "M1 durable article"
    searched = api.get("/content", params={"q": "durable", "limit": 1})
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["next_cursor"] is None
    assert api.get("/content", params={"q": "absent"}).json()["total"] == 0

    detail = api.get("/content/1")
    assert detail.status_code == 200
    assert detail.json()["scores"]["quality"] == 89
    assert detail.json()["base_analysis"]["quotes"] == ["Persistence survives restarts."]
    assert detail.json()["translations"]["zh"]["title"].startswith("ZH:")

    recommendation = api.get("/recommend", params={"user_id": 7, "limit": 1})
    assert recommendation.json()["items"][0]["id"] == "1"
    companion_response = api.get(
        "/companion", params={"content_id": 1, "question": "why?"}
    )
    assert companion_response.status_code == 200
    assert "grounded in the L1 summary" in " ".join(companion_response.json()["chunks"])


def test_http_distinguishes_not_found_upstream_data_and_configuration(
    tmp_path, monkeypatch
) -> None:
    api = client(tmp_path / "complete")
    missing = api.get("/content/404")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "content_not_found"

    missing_l1 = client(tmp_path / "missing-l1", include_l1=False).get("/content/1")
    assert missing_l1.status_code == 502
    assert missing_l1.json()["detail"]["code"] == "upstream_data_error"

    monkeypatch.delenv("L2_DATABASE_URL", raising=False)
    monkeypatch.delenv("L2_L1_DATABASE_URL", raising=False)
    configuration = TestClient(create_app()).get("/content")
    assert configuration.status_code == 503
    assert configuration.json()["detail"]["code"] == "configuration_error"


def test_http_optional_bearer_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("L2_API_KEY", "test-key")
    api = client(tmp_path)
    assert api.get("/content").status_code == 401
    assert api.get(
        "/content", headers={"Authorization": "Bearer test-key"}
    ).status_code == 200


def test_content_search_filters_before_pagination() -> None:
    class SearchRepository:
        def completed_scores(self, vertical: str | None = None) -> list[VerticalScore]:
            del vertical
            return [
                VerticalScore(
                    content_id=1,
                    vertical_code="ai-coding",
                    relevance=90,
                    dim_scores={dimension: 90 for dimension in DIMENSIONS},
                    vertical_tags=[],
                    quality_score=99,
                    reviewed=False,
                    rubric_version="v1",
                    model="fake",
                ),
                VerticalScore(
                    content_id=2,
                    vertical_code="ai-coding",
                    relevance=80,
                    dim_scores={dimension: 80 for dimension in DIMENSIONS},
                    vertical_tags=[],
                    quality_score=80,
                    reviewed=False,
                    rubric_version="v1",
                    model="fake",
                ),
            ]

    service = ContentReadService(SearchRepository(), object())  # type: ignore[arg-type]
    details = {
        1: {
            "id": "1",
            "title": "High score unrelated",
            "summary": "No matching phrase",
            "quality": 99,
        },
        2: {
            "id": "2",
            "title": "Needle article",
            "summary": "The requested result",
            "quality": 80,
        },
    }

    def detail(content_id: int, _score: VerticalScore) -> ContentDetail:
        item = details[content_id]
        return ContentDetail(
            id=item["id"],
            title=item["title"],
            source="Test",
            url=f"https://example.test/{content_id}",
            vertical="ai-coding",
            published_at=datetime(2026, 9, content_id, tzinfo=UTC),
            summary=item["summary"],
            scores={"quality": item["quality"]},
            base_analysis={},
        )

    service._detail = detail  # type: ignore[method-assign]
    page = service.list_content(
        vertical=None,
        status="COMPLETED",
        cursor=None,
        limit=1,
        sort="score",
        query=" needle ",
    )
    assert [item.id for item in page.items] == ["2"]
    assert page.total == 1
    assert page.next_cursor is None
