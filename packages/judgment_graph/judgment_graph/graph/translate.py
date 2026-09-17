from __future__ import annotations

from typing import Literal

from judgment_graph.contracts import BaseAnalysis, Translation
from judgment_graph.llm import LLMClient


def translate_content(
    analysis: BaseAnalysis, llm: LLMClient, lang: Literal["zh", "en"]
) -> Translation:
    terms = llm.complete_json("identify_terms", {"text": analysis.text or analysis.summary})["terms"]
    translated = llm.complete_json(
        "translate",
        {
            "lang": lang,
            "title": analysis.title,
            "summary": analysis.summary,
            "key_points": analysis.key_points,
            "terms": terms,
        },
    )
    check = llm.complete_json("translation_check", {"lang": lang, "fields": translated["fields"], "terms": terms})
    if not check["ok"]:
        raise ValueError(f"translation failed check: {check['comment']}")
    return Translation(
        content_id=analysis.content_id,
        lang=lang,
        fields={key: str(value) for key, value in translated["fields"].items()},
        model=str(getattr(llm, "model_name", "unknown")),
        terms={str(k): str(v) for k, v in terms.items()},
    )


def translate_bilingual(analysis: BaseAnalysis, llm: LLMClient) -> list[Translation]:
    if not getattr(llm, "supports_translation", True):
        return []
    return [translate_content(analysis, llm, "zh"), translate_content(analysis, llm, "en")]

