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
    source_run_id: str | None = None,
    source_revision: int | None = None,
) -> None:
    if (source_run_id is None) != (source_revision is None):
        raise ValueError("source_run_id and source_revision must be provided together")
    if source_run_id is not None and source_revision is not None:
        if not repository.begin_version(content_id, source_run_id, source_revision):
            return
        analysis = provider.get_for_run(content_id, source_run_id)
    else:
        if repository.get_status(content_id) in {"COMPLETED", "CANCELLED", "WAIT_REVIEW"}:
            return
        analysis = provider.get(content_id)
    lens = lens_loader.load_lens(vertical)
    repository.set_status(
        content_id, "WAIT_SCORE", source_revision=source_revision
    )
    result = score_content(analysis, lens, llm)
    if result.skipped:
        repository.set_status(
            content_id, "CANCELLED", source_revision=source_revision
        )
        return
    assert result.score is not None
    repository.persist_score(result.score, source_revision=source_revision)
    for translation in translate_bilingual(analysis, llm):
        repository.persist_translation(
            translation, source_revision=source_revision
        )
    route_review(
        result.score,
        analysis,
        repository,
        source_revision=source_revision,
    )
