"""Explainable offline ranking and extractive reading aids, not an LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from judgment_graph.contracts import BaseAnalysis


@dataclass
class HeuristicProvider:
    model_name: str = "heuristic-v1"
    supports_translation: bool = False

    def complete_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        if task == "score_6dim":
            analysis: BaseAnalysis = payload["analysis"]
            text = analysis.text.casefold()
            words = re.findall(r"\w+", text)
            concepts = sum(
                bool(re.search(rf"\b{term}\b", text))
                for term in ("agent", "agents", "code", "coding", "developer", "model", "ai", "llm")
            )
            practical = sum(
                term in text for term in ("example", "github", "install", "python", "api", "test", "how to")
            )
            content = min(95, 45 + len(words) // 20)
            depth = min(90, 40 + len(words) // 25)
            expression = min(90, 60 + len(analysis.key_points) * 6)
            relevance = min(95, 65 + concepts * 8)
            practical_score = min(95, 55 + practical * 8)
            dimensions = {
                "topic": relevance, "content": content, "depth": depth,
                "practical": practical_score, "novelty": 50, "expression": expression,
            }
            quality = round(content * .35 + depth * .25 + practical_score * .25 + expression * .15)
            return {"relevance": relevance, "dim_scores": dimensions, "quality_score": quality}
        if task == "vertical_tag":
            allowed = set(payload["allowed_tags"])
            return {"tags": [tag for tag in payload.get("content_tags", []) if tag in allowed]}
        if task == "reflect_check":
            return {"needs_revision": False, "comment": "Rule-based reading priority; not expert review or a novelty assessment."}
        if task == "refine":
            return {
                "dim_scores": payload["dim_scores"], "quality_score": payload["quality_score"],
                "note": "heuristic-v1: length, topic terms, practical signals; novelty is neutral (50).",
            }
        raise ValueError(f"heuristic provider does not support {task}")

    def stream_text(self, task: str, payload: dict[str, Any]) -> list[str]:
        if task != "companion":
            raise ValueError(f"heuristic provider does not support {task}")
        ignored = {"the", "what", "how", "why", "and", "for", "this", "that", "does", "are"}
        terms = set(re.findall(r"\w+", str(payload.get("question", "")).casefold())) - ignored
        passages = list(dict.fromkeys([str(payload.get("summary", "")), *payload.get("key_points", [])]))
        ranked = sorted(
            ((len(terms & set(re.findall(r"\w+", passage.casefold()))), passage) for passage in passages if passage),
            key=lambda pair: -pair[0],
        )
        matches = [passage for count, passage in ranked if count][:3]
        if not matches:
            return ["No matching excerpt was found. This offline reading aid cannot generate an answer; please consult the original article."]
        return ["Extractive reading aid — saved passages, not a generated answer.\n", *[
            f"[{index}] {passage}\n" for index, passage in enumerate(matches, 1)
        ]]
