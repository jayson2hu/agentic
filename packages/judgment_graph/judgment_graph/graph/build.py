from __future__ import annotations

from judgment_graph.graph.scoring import score_content
from judgment_graph.graph.translate import translate_bilingual
from judgment_graph.input.provider import AnalysisProvider
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.llm import LLMClient
from judgment_graph.persist.contracts import JudgmentRepository
from judgment_graph.review.queue import route_review


def run_content_pipeline(
    content_id: int,
    vertical: str,
    provider: AnalysisProvider,
    lens_loader: FileLensLoader,
    llm: LLMClient,
    repository: JudgmentRepository,
) -> None:
    # The frozen event identifies content only; duplicate deliveries must not reopen it.
    # Explicit versioned reprocessing is a separate, currently unsupported operation.
    if repository.get_status(content_id) in {"COMPLETED", "CANCELLED", "WAIT_REVIEW"}:
        return
    analysis = provider.get(content_id)
    lens = lens_loader.load_lens(vertical)
    repository.set_status(content_id, "WAIT_SCORE")
    result = score_content(analysis, lens, llm)
    if result.skipped:
        repository.set_status(content_id, "CANCELLED")
        return
    assert result.score is not None
    repository.persist_score(result.score)
    for translation in translate_bilingual(analysis, llm):
        repository.persist_translation(translation)
    route_review(result.score, analysis, repository)
