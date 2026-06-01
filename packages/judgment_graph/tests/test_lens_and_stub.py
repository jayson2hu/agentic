from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.lens.loader import load_lens


def test_stub_provider_returns_fixture() -> None:
    analysis = StubAnalysisProvider().get(1001)
    assert analysis.content_id == 1001
    assert "LangGraph" in analysis.entities


def test_load_ai_coding_lens() -> None:
    lens = load_lens("ai-coding")
    assert lens.code == "ai-coding"
    assert lens.rubric_version == "aic-v1"
    assert "agent-engineering" in lens.allowed_tags

