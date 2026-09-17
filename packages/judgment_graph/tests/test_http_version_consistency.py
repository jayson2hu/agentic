from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from judgment_graph.contracts import Translation
from judgment_graph.http_api import ContentReadService, create_app
from judgment_graph.llm import FakeLLM
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import create_engine
from test_versioned_documents import historical_provider, score

__all__ = ["historical_provider"]  # Re-export the shared L1 history fixture for pytest.


@pytest.fixture
def read_state(tmp_path, historical_provider):
    repository = SqlAlchemyJudgmentRepository(create_engine(f"sqlite:///{tmp_path / 'read-l2.db'}"))
    repository.create_schema()
    repository.begin_version(1, "run-1", 1)
    repository.persist_score(score(), source_revision=1)
    repository.persist_translation(
        Translation(
            content_id=1, lang="zh", fields={"title": "Accepted translation"},
            model="fake-l2-model",
        ),
        source_revision=1,
    )
    repository.mark_completed(1, source_revision=1)
    yield repository, historical_provider
    repository.engine.dispose()


def advance(repository, *, complete: bool) -> None:
    repository.begin_version(1, "run-2", 2)
    if complete:
        repository.persist_score(replace(score(), quality_score=95, model="heuristic-v1"), source_revision=2)
        repository.persist_translation(
            Translation(
                content_id=1, lang="zh", fields={"title": "New revision translation"},
                model="model-name-not-verified",
            ),
            source_revision=2,
        )
        repository.mark_completed(1, source_revision=2)


def assert_version_conflict(response) -> None:
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "content_version_changed"
    assert response.json()["detail"]["retryable"] is True
    assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize("path", ["/content/1", "/content", "/recommend?user_id=1"])
@pytest.mark.parametrize("stage", ["version", "scores", "document", "translation"])
@pytest.mark.parametrize("complete_new_revision", [False, True])
def test_reads_reject_revision_changes_at_each_product_boundary(
    read_state, monkeypatch, path, stage, complete_new_revision
) -> None:
    repository, provider = read_state
    target, method = {
        "version": (repository, "get_source_version"),
        "scores": (repository, "completed_scores"),
        "document": (provider, "get_document_for_run"),
        "translation": (repository, "translation"),
    }[stage]
    original = getattr(target, method)
    triggered = False

    def interleave(*args, **kwargs):
        nonlocal triggered
        value = original(*args, **kwargs)
        if not triggered:
            triggered = True
            advance(repository, complete=complete_new_revision)
        return value

    monkeypatch.setattr(target, method, interleave)
    api = TestClient(create_app(ContentReadService(repository, provider)))
    assert_version_conflict(api.get(path))
    assert triggered


@pytest.mark.parametrize("path", ["/content/1", "/content", "/recommend?user_id=1"])
def test_snapshot_failure_during_revision_change_is_retryable_not_missing(read_state, monkeypatch, path) -> None:
    repository, provider = read_state

    def missing_after_update(content_id, run_id):
        advance(repository, complete=False)
        raise KeyError("history not available during revision change")

    monkeypatch.setattr(provider, "get_document_for_run", missing_after_update)
    api = TestClient(create_app(ContentReadService(repository, provider)))
    assert_version_conflict(api.get(path))


def test_stable_unjudged_l1_projection_cannot_replace_accepted_content(read_state) -> None:
    repository, provider = read_state
    assert provider.get_document(1).analysis.title == "Unjudged revision two"
    api = TestClient(create_app(ContentReadService(repository, provider)))

    detail = api.get("/content/1")
    assert detail.status_code == 200
    assert detail.json()["title"] == "M1 durable article"
    assert detail.json()["summary"] == "Persisted L1 summary"
    assert detail.json()["translations"]["zh"]["title"] == "Accepted translation"
    assert detail.json()["provenance"]["translation_available"] is True
    for path in ("/content", "/recommend?user_id=1"):
        response = api.get(path)
        assert response.status_code == 200
        assert response.json()["items"][0]["title"] == "M1 durable article"
    assert api.get("/content", params={"q": "Unjudged"}).json()["total"] == 0
    assert api.get("/content", params={"q": "Persisted", "limit": 1}).json()["total"] == 1

    answer = api.get("/companion", params={"content_id": 1, "question": "context"})
    assert answer.status_code == 200
    assert "M1 durable article" in "".join(answer.json()["chunks"])
    assert "Unjudged" not in "".join(answer.json()["chunks"])


def test_companion_rechecks_version_after_generating_chunks(read_state) -> None:
    repository, provider = read_state

    class UpdatingLLM(FakeLLM):
        def stream_text(self, task, payload):
            assert payload["title"] == "M1 durable article"
            advance(repository, complete=True)
            return ["data: Answer computed from the old snapshot."]

    api = TestClient(create_app(ContentReadService(repository, provider, UpdatingLLM())))
    assert_version_conflict(api.get("/companion", params={"content_id": 1, "question": "why?"}))


def test_status_change_without_new_identity_is_retryable(read_state, monkeypatch) -> None:
    repository, provider = read_state
    original = repository.get_status
    calls = 0

    def status_after_start(content_id):
        nonlocal calls
        calls += 1
        return original(content_id) if calls == 1 else "WAIT_SCORE"

    monkeypatch.setattr(repository, "get_status", status_after_start)
    api = TestClient(create_app(ContentReadService(repository, provider)))
    assert_version_conflict(api.get("/content/1"))


@pytest.mark.parametrize("model,expected", [
    ("FAKE-L2-MODEL", "simulated"),
    ("FakeLLM", "simulated"),
    (" fake ", "simulated"),
    ("heuristic-v1", "heuristic"),
    ("Heuristic-v2", "heuristic"),
    ("future-provider-without-verification", "unknown"),
])
def test_model_names_do_not_claim_verified_analysis(read_state, model, expected) -> None:
    repository, provider = read_state
    repository.begin_version(1, "run-1", 2)
    repository.persist_score(replace(score(), model=model), source_revision=2)
    repository.mark_completed(1, source_revision=2)
    api = TestClient(create_app(ContentReadService(repository, provider)))

    response = api.get("/content/1")
    assert response.status_code == 200
    assert response.json()["provenance"]["scoring_method"] == expected


def test_missing_content_and_missing_accepted_snapshot_keep_distinct_errors(read_state, monkeypatch) -> None:
    repository, provider = read_state
    api = TestClient(create_app(ContentReadService(repository, provider)))
    assert api.get("/content/404").status_code == 404

    def missing_history(content_id, run_id):
        raise KeyError("missing accepted history")

    monkeypatch.setattr(provider, "get_document_for_run", missing_history)
    response = api.get("/content/1")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "upstream_data_error"
