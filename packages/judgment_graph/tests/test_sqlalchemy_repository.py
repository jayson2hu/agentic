from judgment_graph.contracts import Translation, VerticalScore
from judgment_graph.graph.recommend import recommend
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from judgment_graph.scripts.seed_verticals import load_ai_coding_seed, seed_ai_coding_lens
from sqlalchemy import create_engine


def score(content_id: int = 7) -> VerticalScore:
    return VerticalScore(
        content_id=content_id,
        vertical_code="ai-coding",
        relevance=88,
        dim_scores={
            "topic": 90,
            "content": 88,
            "depth": 84,
            "practical": 92,
            "novelty": 80,
            "expression": 86,
        },
        vertical_tags=["agent-engineering", "developer-tools"],
        quality_score=87,
        reviewed=False,
        rubric_version="aic-v1",
        model="fake-l2-model",
    )


def repository() -> SqlAlchemyJudgmentRepository:
    repo = SqlAlchemyJudgmentRepository(create_engine("sqlite+pysqlite:///:memory:", future=True))
    repo.create_schema()
    return repo


def test_metadata_only_declares_l2_owned_tables() -> None:
    assert set(models.metadata.tables) == {
        "verticals",
        "content_vertical_scores",
        "content_translations",
        "review_queue",
        "content_judgment_state",
        "judgment_outbox",
    }
    assert set(models.content_translations.c.keys()) == {"content_id", "lang", "fields", "model"}


def test_sqlalchemy_repository_persists_products_and_recommends() -> None:
    repo = repository()
    repo.set_status(7, "WAIT_SCORE")
    repo.persist_score(score())
    repo.persist_translation(
        Translation(
            content_id=7,
            lang="en",
            fields={"title": "EN: title", "summary": "EN: summary"},
            model="fake-l2-model",
            terms={"LLM": "LLM"},
        )
    )
    repo.mark_completed(7)

    assert repo.translation(7, "en") is not None
    assert [event.type for event in repo.outbox()] == ["content.completed"]
    refs = recommend(1, "ai-coding", 10, repo, {1: {"agent-engineering": 1.0}})
    assert [ref.content_id for ref in refs] == [7]
    assert repo.cost_units[7] == 2


def test_sqlalchemy_repository_seeds_ai_coding_lens() -> None:
    repo = repository()
    seed_ai_coding_lens(repo)

    lens = repo.load_lens("ai-coding")
    assert lens.code == "ai-coding"
    assert lens.rubric_version == "aic-v1"
    assert load_ai_coding_seed()["rubric_version"] == "aic-v1"


def test_sqlalchemy_review_decision_closes_loop() -> None:
    repo = repository()
    repo.set_status(8, "WAIT_SCORE")
    repo.persist_score(score(content_id=8))
    item = repo.enqueue_review(8, "ai-coding", "low_confidence_band")
    assert repo.get_status(8) == "WAIT_REVIEW"

    repo.decide_review(item.id, "approved", "owner", "ok")
    assert repo.get_status(8) == "COMPLETED"
    assert repo.completed_scores("ai-coding")[0].reviewed is True
