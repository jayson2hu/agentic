from judgment_graph.graph.langgraph_build import build_scoring_graph
from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import load_lens
from judgment_graph.llm import FakeLLM
import pytest


def maybe_graph():
    try:
        return build_scoring_graph()
    except TypeError as exc:
        pytest.skip(f"local langgraph dependency is incompatible: {exc}")


def test_langgraph_scoring_graph_executes_score_reflect_refine_path() -> None:
    graph = maybe_graph()
    state = graph.invoke(
        {
            "analysis": StubAnalysisProvider().get(1001),
            "lens": load_lens("ai-coding"),
            "llm": FakeLLM(),
        }
    )

    assert state["skipped"] is False
    assert state["score"].quality_score > 70
    assert state["score"].reflection == "domain-review: consistent"


def test_langgraph_scoring_graph_skips_irrelevant_content() -> None:
    graph = maybe_graph()
    state = graph.invoke(
        {
            "analysis": StubAnalysisProvider().get(1002),
            "lens": load_lens("ai-coding"),
            "llm": FakeLLM(),
        }
    )

    assert state["skipped"] is True
    assert state["score"] is None
