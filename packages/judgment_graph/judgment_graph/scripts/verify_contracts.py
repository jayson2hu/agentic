from __future__ import annotations

import ast
import json
from pathlib import Path

from judgment_graph.persist import models
from judgment_graph.scripts.feature_matrix import verify_feature_self_tests

ROOT = Path(__file__).resolve().parents[4]
ALLOWED_L2_TABLES = {
    "verticals",
    "content_vertical_scores",
    "content_translations",
    "review_queue",
    "content_judgment_state",
    "judgment_outbox",
}
FORBIDDEN_LLM_MODULES = {
    "anthropic",
    "dashscope",
    "google.generativeai",
    "openai",
}
REQUIRED_FEATURE_MARKERS = {
    "F0.1": "judgment_graph/workers/scoring/worker.py",
    "F0.1-enqueue": "judgment_graph/workers/scoring/enqueue.py",
    "F0.2": "judgment_graph/llm.py",
    "F0.3": "db/alembic/versions/20260530_0001_l2_judgment_tables.py",
    "F1.1": "judgment_graph/input/provider.py",
    "F1.1-factory": "judgment_graph/input/factory.py",
    "F1.1-sqlalchemy": "judgment_graph/input/sqlalchemy_provider.py",
    "F1.2": "tests/fixtures/analysis/base_analysis.json",
    "F1.3": "judgment_graph/events/consume.py",
    "F2.1": "judgment_graph/lens/loader.py",
    "F2.2": "judgment_graph/config/verticals/ai-coding.json",
    "F2.2-seed": "judgment_graph/scripts/seed_verticals.py",
    "F2.3": "judgment_graph/graph/scoring.py",
    "F3.1": "judgment_graph/graph/scoring.py",
    "F3.2": "judgment_graph/graph/scoring.py",
    "F3.3": "judgment_graph/graph/scoring.py",
    "F3.4": "judgment_graph/graph/scoring.py",
    "F4.1": "judgment_graph/graph/translate.py",
    "F4.2": "judgment_graph/graph/translate.py",
    "F5.1": "judgment_graph/review/queue.py",
    "F5.2": "judgment_graph/persist/repository.py",
    "F6.1": "judgment_graph/graph/recommend.py",
    "F6.2": "judgment_graph/graph/recommend.py",
    "F6.3": "judgment_graph/persist/repository.py",
    "F7.1": "judgment_graph/graph/companion.py",
    "F7.2": "judgment_graph/service.py",
    "F8.1": "judgment_graph/persist/repository.py",
    "F8.2": "judgment_graph/persist/repository.py",
    "F8.3": "judgment_graph/events/emit.py",
    "F9.1": "judgment_graph/persist/repository.py",
    "F9.2": "judgment_graph/persist/repository.py",
    "integration-env": "docker-compose.integration.yml",
    "integration-check": "judgment_graph/scripts/integration_check.py",
    "ci-workflow": ".github/workflows/l2.yml",
}


def verify_owned_tables() -> None:
    declared = set(models.metadata.tables)
    if declared != ALLOWED_L2_TABLES:
        raise AssertionError(f"L2 table set mismatch: {declared}")


def verify_no_l3_imports() -> None:
    verify_no_forbidden_imports({"l3"}, "L3")


def verify_no_real_llm_clients() -> None:
    package_root = ROOT / "packages" / "judgment_graph" / "judgment_graph"
    pyproject = ROOT / "pyproject.toml"
    dependency_text = pyproject.read_text(encoding="utf-8").lower()
    forbidden_dependencies = [
        name for name in FORBIDDEN_LLM_MODULES if name.lower() in dependency_text
    ]
    if forbidden_dependencies:
        raise AssertionError(f"Real LLM dependencies forbidden: {forbidden_dependencies}")

    verify_no_forbidden_imports(FORBIDDEN_LLM_MODULES, "real LLM client")

    for path in (package_root / "config" / "verticals").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        model_profile = raw.get("model_profile", {})
        models_used = [str(value) for value in dict(model_profile).values()]
        non_fake = [value for value in models_used if not value.startswith("fake-")]
        if non_fake:
            raise AssertionError(f"Real model profile forbidden in {path}: {non_fake}")


def verify_no_forbidden_imports(forbidden_roots: set[str], label: str) -> None:
    package_root = ROOT / "packages" / "judgment_graph" / "judgment_graph"
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(_matches_forbidden_import(name, forbidden_roots) for name in names):
                raise AssertionError(f"{label} import forbidden in {path}: {names}")


def _matches_forbidden_import(name: str, forbidden_roots: set[str]) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in forbidden_roots)


def verify_feature_markers() -> None:
    package_root = ROOT / "packages" / "judgment_graph"
    missing: list[str] = []
    for feature, relative in REQUIRED_FEATURE_MARKERS.items():
        base = ROOT if relative.startswith(("db/", "docker-compose", ".github/")) else package_root
        path = (base / relative).resolve()
        if not path.exists():
            missing.append(f"{feature}:{relative}")
    if missing:
        raise AssertionError("Missing feature artifacts: " + ", ".join(missing))


def main() -> None:
    verify_owned_tables()
    verify_no_l3_imports()
    verify_no_real_llm_clients()
    verify_feature_markers()
    verify_feature_self_tests()
    print("L2 CONTRACTS: PASS")


if __name__ == "__main__":
    main()
