from __future__ import annotations

from judgment_graph.contracts import ContentRef
from judgment_graph.persist.contracts import JudgmentRepository


def recommend(
    user_id: int,
    vertical: str,
    limit: int,
    repository: JudgmentRepository,
    user_tag_weights: dict[int, dict[str, float]] | None = None,
) -> list[ContentRef]:
    weights = (user_tag_weights or {}).get(user_id, {})
    refs: list[ContentRef] = []
    for score in repository.completed_scores(vertical):
        tag_boost = sum(weights.get(tag, 0.0) for tag in score.vertical_tags)
        freshness = 1.0 / (1 + score.content_id)
        rank = score.quality_score * 0.7 + score.relevance * 0.2 + tag_boost * 10 + freshness
        refs.append(repository.as_content_ref(score, rank))
    refs.sort(key=lambda item: (-item.rank_score, item.content_id))
    return refs[:limit]
