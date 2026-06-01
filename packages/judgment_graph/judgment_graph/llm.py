from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def stream_text(self, task: str, payload: dict[str, Any]) -> list[str]: ...


@dataclass
class FakeLLM:
    """Deterministic offline LLM for graph tests and smoke runs."""

    model_name: str = "fake-l2-model"

    def complete_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        if task == "score_6dim":
            relevant = bool(payload.get("relevant", True))
            base = 82 if relevant else 12
            return {
                "relevance": 90 if relevant else 8,
                "dim_scores": {
                    "topic": base,
                    "content": base + 3 if relevant else base,
                    "depth": base - 4 if relevant else base,
                    "practical": base + 5 if relevant else base,
                    "novelty": base - 2 if relevant else base,
                    "expression": base,
                },
                "quality_score": base + 1 if relevant else 10,
            }
        if task == "vertical_tag":
            allowed = list(payload["allowed_tags"])
            preferred = [tag for tag in payload.get("content_tags", []) if tag in allowed]
            return {"tags": preferred[:3] or allowed[:2]}
        if task == "reflect_check":
            score = int(payload.get("quality_score", 0))
            return {
                "needs_revision": score < 70,
                "comment": "domain-review: consistent" if score >= 70 else "domain-review: low confidence",
            }
        if task == "refine":
            if payload.get("needs_revision"):
                dim_scores = {key: min(100, int(value) + 5) for key, value in payload["dim_scores"].items()}
                quality = min(100, int(payload["quality_score"]) + 5)
                return {"dim_scores": dim_scores, "quality_score": quality, "note": "raised after review"}
            return {
                "dim_scores": payload["dim_scores"],
                "quality_score": payload["quality_score"],
                "note": "no change",
            }
        if task == "identify_terms":
            terms = {
                term: term
                for term in ("LangGraph", "LLM", "pgvector", "SQLAlchemy", "Alembic")
                if term.lower() in str(payload.get("text", "")).lower()
            }
            return {"terms": terms}
        if task == "translate":
            lang = payload["lang"]
            prefix = "ZH" if lang == "zh" else "EN"
            return {
                "fields": {
                    "title": f"{prefix}: {payload['title']}",
                    "summary": f"{prefix}: {payload['summary']}",
                    "key_points": f"{prefix}: " + " | ".join(payload.get("key_points", [])),
                }
            }
        if task == "translation_check":
            return {"ok": True, "comment": "terminology-consistent"}
        raise ValueError(f"unknown fake task: {task}")

    def stream_text(self, task: str, payload: dict[str, Any]) -> list[str]:
        if task != "companion":
            raise ValueError(f"unknown stream task: {task}")
        title = payload.get("title", "content")
        question = payload.get("question", "")
        return [
            f"data: Based on {title}, ",
            f"data: the answer to '{question}' is grounded in the L1 summary. ",
            "data: Key points were used as supporting context.\n\n",
        ]

