from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from judgment_graph.contracts import BaseAnalysis
from judgment_graph.graph.translate import translate_bilingual
from judgment_graph.heuristic import HeuristicProvider
from judgment_graph.http_api import ContentReadService, create_app
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider, create_l1_engine
from judgment_graph.llm import FakeLLM, create_llm
from judgment_graph.persist.sqlalchemy_repository import (
    SqlAlchemyJudgmentRepository,
    create_sqlalchemy_engine,
)
from judgment_graph.scripts.prepare_preview import prepare
from sqlalchemy import select, update
from test_http_api import seed_l1


def analysis() -> BaseAnalysis:
    return BaseAnalysis(
        content_id=1, title="Practical AI coding agents", source="Engineering blog",
        language="en", summary="Developers test agent code with a Python API.",
        key_points=["Developers test agent code with a Python API.", "Install the example from GitHub.", "Measure the model."],
        entities=[], tags=["agent-engineering"], embedding=[0.1, 0.2],
        text="An AI model helps a developer test coding agents using a Python API and GitHub example. " * 100,
    )


def test_rules_depend_on_article_evidence_and_do_not_translate() -> None:
    rules = HeuristicProvider()
    long = rules.complete_json("score_6dim", {"analysis": analysis()})
    short = rules.complete_json("score_6dim", {"analysis": replace(analysis(), text="Brief note.")})
    assert long["quality_score"] > short["quality_score"]
    assert long["relevance"] > short["relevance"]
    assert long["dim_scores"]["novelty"] == 50  # Unmeasured, never presented as model judgment.
    assert translate_bilingual(analysis(), rules) == []
    assert rules.complete_json("vertical_tag", {"allowed_tags": ["a"], "content_tags": ["a", "invented"]}) == {"tags": ["a"]}
    assert rules.complete_json("reflect_check", {})["needs_revision"] is False
    refined = rules.complete_json("refine", long)
    assert refined["quality_score"] == long["quality_score"]
    with pytest.raises(ValueError, match="does not support"):
        rules.complete_json("translate", {})


def test_reading_aid_returns_source_excerpts_or_explicit_no_match() -> None:
    rules = HeuristicProvider()
    result = "".join(rules.stream_text("companion", {
        "summary": analysis().summary, "key_points": analysis().key_points,
        "question": "How do developers test code?",
    }))
    assert "not a generated answer" in result
    assert analysis().summary in result
    assert "No matching excerpt" in rules.stream_text("companion", {"question": "unicorn", "summary": "Python API"})[0]
    with pytest.raises(ValueError):
        rules.stream_text("unsupported", {})


def test_mode_selection_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("L2_PROCESSING_MODE", raising=False)
    assert isinstance(create_llm(), FakeLLM)
    monkeypatch.setenv("L2_PROCESSING_MODE", "heuristic")
    assert isinstance(create_llm(), HeuristicProvider)
    monkeypatch.setenv("L2_PROCESSING_MODE", "typo")
    with pytest.raises(ValueError, match="L2_PROCESSING_MODE"):
        create_llm()


def test_real_snapshot_to_durable_rules_http_and_restart(tmp_path) -> None:
    l1 = tmp_path / "l1.db"
    l2 = tmp_path / "l2.db"
    provider = seed_l1(f"sqlite:///{l1}")
    table = provider.content_base_analysis
    with provider.engine.begin() as connection:
        row = connection.execute(select(table)).mappings().one()
        snapshot = dict(row["input_snapshot"])
        snapshot["title"] = analysis().title
        snapshot["body"] = analysis().text
        snapshot["metadata"] = {
            "source": {"name": "Engineering blog"}, "lang": "en",
            "source_kind": "public_feed", "processing": {"method": "extractive-v1"},
        }
        payload = dict(row["analysis"])
        payload.update(summary=analysis().summary, key_points=analysis().key_points)
        connection.execute(update(table).values(input_snapshot=snapshot, analysis=payload))
        connection.exec_driver_sql("ALTER TABLE content_base_analysis ADD COLUMN revision INTEGER DEFAULT 1")
        connection.exec_driver_sql(
            "CREATE TABLE l1_processing_runs (content_id TEXT, run_id TEXT, input_snapshot JSON, "
            "analysis JSON, graph_version TEXT, content_hash TEXT, status TEXT, finished_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO l1_processing_runs SELECT content_id, run_id, input_snapshot, analysis, "
            "graph_version, content_hash, status, updated_at FROM content_base_analysis"
        )
    report = prepare(l1, l2)
    assert report["completed"] == 1
    assert prepare(l1, l2)["items"] == report["items"]  # Repeat is idempotent.
    repository = SqlAlchemyJudgmentRepository(create_sqlalchemy_engine(f"sqlite:///{l2}"))
    reloaded = SqlAlchemyAnalysisProvider(create_l1_engine(f"sqlite:///{l1}"))
    api = TestClient(create_app(ContentReadService(repository, reloaded, HeuristicProvider())))
    assert api.get("/", follow_redirects=False).headers["location"] == "/docs"
    item = api.get("/content/1").json()
    assert item["translations"] == {}
    assert item["language"] == "en"
    assert item["reading_minutes"] > 1
    assert item["provenance"]["analysis_method"] == "extractive-v1"
    assert item["provenance"]["scoring_method"] == "heuristic"
    assert item["provenance"]["translation_available"] is False
    assert item["provenance"]["reviewed"] is False
    assert api.get("/content", params={"q": "developers"}).json()["total"] == 1
    assert api.get("/content", params={"q": "unicorn"}).json()["total"] == 0
    assert "saved passages" in "".join(api.get("/companion", params={"content_id": 1, "question": "test code"}).json()["chunks"])
    with pytest.raises(ValueError, match="different"):
        prepare(l1, l1)
    with pytest.raises(ValueError, match="already exist"):
        prepare(tmp_path / "missing.db", l2)
