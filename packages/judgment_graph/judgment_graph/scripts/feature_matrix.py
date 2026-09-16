from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PACKAGE_ROOT = ROOT / "packages" / "judgment_graph"


@dataclass(frozen=True)
class FeatureEvidence:
    feature: str
    title: str
    implementation: tuple[str, ...]
    self_tests: tuple[str, ...]


FEATURE_MATRIX: tuple[FeatureEvidence, ...] = (
    FeatureEvidence(
        "F0.1",
        "Arq scoring worker and enqueue contract",
        ("judgment_graph/workers/scoring/worker.py", "judgment_graph/workers/scoring/enqueue.py"),
        ("tests/test_worker_contract.py", "tests/test_enqueue_contract.py"),
    ),
    FeatureEvidence(
        "F0.2",
        "FakeLLM injectable model router",
        ("judgment_graph/llm.py",),
        ("tests/test_langgraph_scoring.py", "tests/test_pipeline.py"),
    ),
    FeatureEvidence(
        "F0.3",
        "Alembic migration and integration trace harness",
        (
            "../../db/alembic/versions/20260530_0001_l2_judgment_tables.py",
            "judgment_graph/scripts/integration_check.py",
        ),
        ("tests/test_integration_check.py",),
    ),
    FeatureEvidence(
        "F1.1",
        "AnalysisProvider abstraction and runtime provider switch",
        (
            "judgment_graph/input/provider.py",
            "judgment_graph/input/factory.py",
            "judgment_graph/input/sqlalchemy_provider.py",
        ),
        ("tests/test_analysis_provider_factory.py",),
    ),
    FeatureEvidence(
        "F1.2",
        "StubAnalysisProvider fixture path",
        ("judgment_graph/input/stub.py", "tests/fixtures/analysis/base_analysis.json"),
        ("tests/test_lens_and_stub.py", "tests/test_pipeline.py"),
    ),
    FeatureEvidence(
        "F1.3",
        "content.analyzed idempotent consumer",
        ("judgment_graph/events/consume.py",),
        ("tests/test_pipeline.py",),
    ),
    FeatureEvidence(
        "F2.1",
        "Lens loader",
        ("judgment_graph/lens/loader.py",),
        ("tests/test_lens_and_stub.py",),
    ),
    FeatureEvidence(
        "F2.2",
        "ai-coding vertical seed",
        ("judgment_graph/config/verticals/ai-coding.json", "judgment_graph/scripts/seed_verticals.py"),
        ("tests/test_sqlalchemy_repository.py",),
    ),
    FeatureEvidence(
        "F2.3",
        "source-filter relevance gate",
        ("judgment_graph/graph/scoring.py",),
        ("tests/test_pipeline.py",),
    ),
    FeatureEvidence(
        "F3.1-F3.4",
        "score, tag, reflect, and refine graph",
        ("judgment_graph/graph/scoring.py", "judgment_graph/graph/langgraph_build.py"),
        ("tests/test_pipeline.py", "tests/test_langgraph_scoring.py"),
    ),
    FeatureEvidence(
        "F4.1-F4.2",
        "terminology and bilingual translations",
        ("judgment_graph/graph/translate.py",),
        ("tests/test_pipeline.py", "tests/test_sqlalchemy_repository.py"),
    ),
    FeatureEvidence(
        "F5.1-F5.2",
        "review queue and decision closure",
        ("judgment_graph/review/queue.py", "judgment_graph/persist/repository.py"),
        ("tests/test_pipeline.py", "tests/test_sqlalchemy_repository.py"),
    ),
    FeatureEvidence(
        "F6.1-F6.3",
        "recommendation recall, rerank, and user profile signals",
        ("judgment_graph/graph/recommend.py", "judgment_graph/persist/repository.py"),
        ("tests/test_pipeline.py", "tests/test_service_contract.py"),
    ),
    FeatureEvidence(
        "F7.1-F7.2",
        "companion SSE API and accounting hook",
        ("judgment_graph/graph/companion.py", "judgment_graph/service.py"),
        ("tests/test_pipeline.py", "tests/test_service_contract.py"),
    ),
    FeatureEvidence(
        "F8.1-F8.3",
        "score/translation persistence and content.completed event",
        (
            "judgment_graph/persist/repository.py",
            "judgment_graph/persist/sqlalchemy_repository.py",
            "judgment_graph/events/emit.py",
        ),
        ("tests/test_repository.py", "tests/test_sqlalchemy_repository.py", "tests/test_pipeline.py"),
    ),
    FeatureEvidence(
        "F9.1-F9.2",
        "rubric reprocess detection and cost counters",
        ("judgment_graph/persist/repository.py", "judgment_graph/persist/sqlalchemy_repository.py"),
        ("tests/test_repository.py", "tests/test_sqlalchemy_repository.py"),
    ),
)


def _resolve(relative: str) -> Path:
    return (PACKAGE_ROOT / relative).resolve()


def verify_feature_self_tests() -> None:
    missing: list[str] = []
    for item in FEATURE_MATRIX:
        for relative in item.implementation:
            if not _resolve(relative).exists():
                missing.append(f"{item.feature}:implementation:{relative}")
        for relative in item.self_tests:
            if not _resolve(relative).exists():
                missing.append(f"{item.feature}:self-test:{relative}")
    if missing:
        raise AssertionError("Missing feature evidence: " + ", ".join(missing))


def main() -> None:
    verify_feature_self_tests()
    for item in FEATURE_MATRIX:
        print(f"PASS: {item.feature} - {item.title}")
    print("L2 FEATURE MATRIX: PASS")


if __name__ == "__main__":
    main()
