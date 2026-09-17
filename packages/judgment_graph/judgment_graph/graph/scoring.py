from __future__ import annotations

from dataclasses import dataclass

from judgment_graph.contracts import BaseAnalysis, Lens, VerticalScore
from judgment_graph.llm import LLMClient


@dataclass(frozen=True)
class ScoringResult:
    score: VerticalScore | None
    skipped: bool
    reason: str | None = None


def relevance_gate(analysis: BaseAnalysis, lens: Lens) -> bool:
    haystack = " ".join(
        [analysis.title, analysis.summary, analysis.source, *analysis.key_points, *analysis.tags]
    ).lower()
    keywords = [str(k).lower() for k in lens.source_filter.get("keywords", [])]
    sources = [str(s).lower() for s in lens.source_filter.get("sources", [])]
    return any(keyword in haystack for keyword in keywords) or analysis.source.lower() in sources


def score_content(analysis: BaseAnalysis, lens: Lens, llm: LLMClient) -> ScoringResult:
    relevant = relevance_gate(analysis, lens)
    if not relevant:
        return ScoringResult(score=None, skipped=True, reason="source_filter_miss")

    scored = llm.complete_json(
        "score_6dim",
        {"analysis": analysis, "rubric_prompt": lens.rubric_prompt, "relevant": relevant},
    )
    tags_raw = llm.complete_json(
        "vertical_tag",
        {
            "allowed_tags": sorted(lens.allowed_tags),
            "content_tags": analysis.tags,
            "tag_taxonomy": lens.tag_taxonomy,
        },
    )
    tags = [tag for tag in tags_raw["tags"] if tag in lens.allowed_tags]
    reflection = llm.complete_json(
        "reflect_check",
        {"quality_score": scored["quality_score"], "dim_scores": scored["dim_scores"]},
    )
    refined = llm.complete_json(
        "refine",
        {
            "needs_revision": reflection["needs_revision"],
            "quality_score": scored["quality_score"],
            "dim_scores": scored["dim_scores"],
        },
    )
    score = VerticalScore(
        content_id=analysis.content_id,
        vertical_code=lens.code,
        relevance=int(scored["relevance"]),
        dim_scores={key: int(value) for key, value in refined["dim_scores"].items()},
        vertical_tags=tags,
        quality_score=int(refined["quality_score"]),
        reviewed=False,
        review_note=refined.get("note"),
        reflection=str(reflection["comment"]),
        rubric_version=lens.rubric_version,
        model=str(getattr(llm, "model_name", lens.model_profile["standard"])),
    )
    return ScoringResult(score=score, skipped=False)

