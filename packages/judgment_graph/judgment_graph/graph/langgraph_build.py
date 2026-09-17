from __future__ import annotations

from typing import Any, TypedDict

from judgment_graph.contracts import BaseAnalysis, Lens, VerticalScore
from judgment_graph.graph.scoring import relevance_gate
from judgment_graph.llm import LLMClient


class ScoringState(TypedDict, total=False):
    analysis: BaseAnalysis
    lens: Lens
    llm: LLMClient
    relevant: bool
    raw_score: dict[str, Any]
    tags: list[str]
    reflection: dict[str, Any]
    score: VerticalScore | None
    skipped: bool
    reason: str


def build_scoring_graph() -> Any:
    from langgraph.graph import END, StateGraph

    graph = StateGraph(ScoringState)

    def gate(state: ScoringState) -> ScoringState:
        relevant = relevance_gate(state["analysis"], state["lens"])
        if not relevant:
            return {"relevant": False, "skipped": True, "reason": "source_filter_miss"}
        return {"relevant": True, "skipped": False}

    def score_6dim(state: ScoringState) -> ScoringState:
        raw = state["llm"].complete_json(
            "score_6dim",
            {
                "analysis": state["analysis"],
                "rubric_prompt": state["lens"].rubric_prompt,
                "relevant": state["relevant"],
            },
        )
        return {"raw_score": raw}

    def vertical_tag(state: ScoringState) -> ScoringState:
        lens = state["lens"]
        raw = state["llm"].complete_json(
            "vertical_tag",
            {
                "allowed_tags": sorted(lens.allowed_tags),
                "content_tags": state["analysis"].tags,
                "tag_taxonomy": lens.tag_taxonomy,
            },
        )
        return {"tags": [tag for tag in raw["tags"] if tag in lens.allowed_tags]}

    def reflect_check(state: ScoringState) -> ScoringState:
        raw = state["raw_score"]
        reflection = state["llm"].complete_json(
            "reflect_check",
            {"quality_score": raw["quality_score"], "dim_scores": raw["dim_scores"]},
        )
        return {"reflection": reflection}

    def refine(state: ScoringState) -> ScoringState:
        raw = state["raw_score"]
        refined = state["llm"].complete_json(
            "refine",
            {
                "needs_revision": state["reflection"]["needs_revision"],
                "quality_score": raw["quality_score"],
                "dim_scores": raw["dim_scores"],
            },
        )
        lens = state["lens"]
        analysis = state["analysis"]
        return {
            "score": VerticalScore(
                content_id=analysis.content_id,
                vertical_code=lens.code,
                relevance=int(raw["relevance"]),
                dim_scores={key: int(value) for key, value in refined["dim_scores"].items()},
                vertical_tags=state["tags"],
                quality_score=int(refined["quality_score"]),
                reviewed=False,
                review_note=refined.get("note"),
                reflection=str(state["reflection"]["comment"]),
                rubric_version=lens.rubric_version,
                model=str(getattr(state["llm"], "model_name", lens.model_profile["standard"])),
            )
        }

    def after_gate(state: ScoringState) -> str:
        return "skip" if state.get("skipped") else "score_6dim"

    graph.add_node("relevance_gate", gate)
    graph.add_node("score_6dim", score_6dim)
    graph.add_node("vertical_tag", vertical_tag)
    graph.add_node("reflect_check", reflect_check)
    graph.add_node("refine", refine)
    graph.add_node("skip", lambda state: {"score": None})
    graph.set_entry_point("relevance_gate")
    graph.add_conditional_edges(
        "relevance_gate",
        after_gate,
        {"score_6dim": "score_6dim", "skip": "skip"},
    )
    graph.add_edge("score_6dim", "vertical_tag")
    graph.add_edge("vertical_tag", "reflect_check")
    graph.add_edge("reflect_check", "refine")
    graph.add_edge("refine", END)
    graph.add_edge("skip", END)
    return graph.compile()
