from __future__ import annotations

from judgment_graph.contracts import BaseAnalysis, VerticalScore
from judgment_graph.persist.contracts import JudgmentRepository


def route_review(
    score: VerticalScore,
    analysis: BaseAnalysis,
    repository: JudgmentRepository,
    lowconf_band: int = 10,
    exposure_threshold: int = 100_000,
    source_revision: int | None = None,
) -> None:
    border_distance = min(abs(score.quality_score - 70), abs(score.relevance - 70))
    if border_distance <= lowconf_band:
        repository.enqueue_review(
            score.content_id,
            score.vertical_code,
            "low_confidence_band",
            source_revision=source_revision,
        )
        return
    if analysis.exposure >= exposure_threshold:
        repository.enqueue_review(
            score.content_id,
            score.vertical_code,
            "high_exposure",
            source_revision=source_revision,
        )
        return
    repository.mark_completed(
        score.content_id, source_revision=source_revision
    )
