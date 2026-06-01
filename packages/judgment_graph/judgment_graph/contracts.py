from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

DimensionName = Literal["topic", "content", "depth", "practical", "novelty", "expression"]
DIMENSIONS: tuple[DimensionName, ...] = (
    "topic",
    "content",
    "depth",
    "practical",
    "novelty",
    "expression",
)
ContentStatus = Literal["WAIT_SCORE", "WAIT_REVIEW", "COMPLETED", "CANCELLED"]


def require_score(value: int, field_name: str) -> int:
    if not 0 <= value <= 100:
        raise ValueError(f"{field_name} must be in 0..100")
    return value


@dataclass(frozen=True)
class BaseAnalysis:
    content_id: int
    title: str
    source: str
    language: str
    summary: str
    key_points: list[str]
    entities: list[str]
    tags: list[str]
    embedding: list[float]
    text: str = ""
    exposure: int = 0


@dataclass(frozen=True)
class Lens:
    code: str
    name: str
    enabled: bool
    source_filter: dict[str, Any]
    rubric_prompt: str
    rubric_version: str
    tag_taxonomy: dict[str, list[str]]
    brief_template: str
    model_profile: dict[str, Any]

    @property
    def allowed_tags(self) -> set[str]:
        tags: set[str] = set()
        for values in self.tag_taxonomy.values():
            tags.update(values)
        return tags


@dataclass(frozen=True)
class VerticalScore:
    content_id: int
    vertical_code: str
    relevance: int
    dim_scores: dict[str, int]
    vertical_tags: list[str]
    quality_score: int
    reviewed: bool
    rubric_version: str
    model: str
    review_note: str | None = None
    reflection: str | None = None

    def __post_init__(self) -> None:
        require_score(self.relevance, "relevance")
        require_score(self.quality_score, "quality_score")
        expected: set[str] = set(DIMENSIONS)
        actual: set[str] = set(self.dim_scores)
        missing = expected - actual
        extra = actual - expected
        if missing or extra:
            raise ValueError(f"dim_scores mismatch missing={sorted(missing)} extra={sorted(extra)}")
        for key, value in self.dim_scores.items():
            require_score(value, f"dim_scores.{key}")


@dataclass(frozen=True)
class Translation:
    content_id: int
    lang: Literal["zh", "en"]
    fields: dict[str, str]
    model: str
    terms: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewItem:
    id: int
    content_id: int
    vertical_code: str
    reason: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True)
class ContentRef:
    content_id: int
    vertical_code: str
    quality_score: int
    relevance: int
    tags: list[str]
    rank_score: float


@dataclass(frozen=True)
class OutboxEvent:
    type: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
