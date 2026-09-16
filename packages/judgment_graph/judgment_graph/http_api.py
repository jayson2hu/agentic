from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from judgment_graph.contracts import Translation, VerticalScore
from judgment_graph.graph.companion import companion
from judgment_graph.input.provider import ContentDocument, ContentDocumentProvider
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider, create_l1_engine
from judgment_graph.llm import FakeLLM, LLMClient
from judgment_graph.persist.sqlalchemy_repository import (
    SqlAlchemyJudgmentRepository,
    create_sqlalchemy_engine,
)

T = TypeVar("T")


class ConfigurationError(RuntimeError):
    pass


class UpstreamDataError(RuntimeError):
    pass


class ApiTranslation(BaseModel):
    title: str
    summary: str
    base_analysis: dict[str, Any]


class ContentSummary(BaseModel):
    id: str
    title: str
    source: str
    url: str
    vertical: str
    status: str = "COMPLETED"
    published_at: datetime
    thumbnail: str | None = None
    summary: str
    scores: dict[str, int] = Field(default_factory=dict)


class ContentDetail(ContentSummary):
    base_analysis: dict[str, Any]
    translations: dict[str, ApiTranslation] = Field(default_factory=dict)


class ContentPage(BaseModel):
    items: list[ContentSummary]
    next_cursor: str | None = None
    total: int


class ContentReadService:
    def __init__(
        self,
        repository: SqlAlchemyJudgmentRepository,
        provider: ContentDocumentProvider,
        llm: LLMClient | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.llm = llm or FakeLLM()

    def list_content(
        self,
        *,
        vertical: str | None,
        status: str,
        cursor: str | None,
        limit: int,
        sort: str,
    ) -> ContentPage:
        if status != "COMPLETED":
            return ContentPage(items=[], total=0)
        offset = self._offset(cursor)
        scores = self._best_scores(vertical)
        details = [self._detail(score.content_id, score) for score in scores]
        if sort == "score":
            details.sort(key=lambda item: (-item.scores.get("quality", 0), int(item.id)))
        elif sort == "published_at":
            details.sort(key=lambda item: (item.published_at, int(item.id)), reverse=True)
        else:
            raise ValueError("sort must be published_at or score")
        page = details[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(details) else None
        return ContentPage(
            items=[ContentSummary.model_validate(item.model_dump()) for item in page],
            next_cursor=next_cursor,
            total=len(details),
        )

    def get_content(self, content_id: int) -> ContentDetail:
        if self.repository.get_status(content_id) != "COMPLETED":
            raise KeyError(content_id)
        scores = [score for score in self.repository.completed_scores() if score.content_id == content_id]
        if not scores:
            raise UpstreamDataError(f"completed L2 content has no score: {content_id}")
        score = max(scores, key=lambda item: (item.quality_score, item.vertical_code))
        return self._detail(content_id, score)

    def recommend(self, user_id: int, vertical: str | None, limit: int) -> list[ContentSummary]:
        del user_id
        scores = self._best_scores(vertical)
        scores.sort(
            key=lambda score: (
                -(score.quality_score * 0.7 + score.relevance * 0.2),
                score.content_id,
            )
        )
        return [
            ContentSummary.model_validate(self._detail(score.content_id, score).model_dump())
            for score in scores[:limit]
        ]

    def companion(self, content_id: int, question: str) -> list[str]:
        self.get_content(content_id)
        chunks = companion(content_id, question, self.provider, self.llm)
        return [chunk.removeprefix("data: ").strip() for chunk in chunks if chunk.strip()]

    def _best_scores(self, vertical: str | None) -> list[VerticalScore]:
        best: dict[int, VerticalScore] = {}
        for score in self.repository.completed_scores(vertical):
            current = best.get(score.content_id)
            if current is None or (score.quality_score, score.vertical_code) > (
                current.quality_score,
                current.vertical_code,
            ):
                best[score.content_id] = score
        return list(best.values())

    def _detail(self, content_id: int, score: VerticalScore) -> ContentDetail:
        try:
            document = self.provider.get_document(content_id)
        except KeyError as exc:
            raise UpstreamDataError(f"L1 snapshot is missing for completed content: {content_id}") from exc
        scores = {key: int(value) for key, value in score.dim_scores.items()}
        scores.update(
            quality=score.quality_score,
            relevance=score.relevance,
            clarity=score.dim_scores.get("expression", 0),
            impact=score.dim_scores.get("practical", 0),
        )
        return ContentDetail(
            id=str(content_id),
            title=document.analysis.title,
            source=document.analysis.source,
            url=document.url,
            vertical=score.vertical_code,
            published_at=document.published_at,
            thumbnail=document.thumbnail,
            summary=document.analysis.summary,
            scores=scores,
            base_analysis={
                "summary": document.analysis.summary,
                "viewpoints": document.analysis.key_points,
                "quotes": document.quotes or [document.analysis.summary],
            },
            translations=self._translations(content_id, document),
        )

    def _translations(
        self, content_id: int, document: ContentDocument
    ) -> dict[str, ApiTranslation]:
        result: dict[str, ApiTranslation] = {}
        for language in ("en", "zh"):
            translation = self.repository.translation(content_id, language)
            if translation is not None:
                result[language] = self._translation(translation, document)
        return result

    def _translation(
        self, translation: Translation, document: ContentDocument
    ) -> ApiTranslation:
        fields = translation.fields
        summary = fields.get("summary", document.analysis.summary)
        points = [
            point.strip()
            for point in fields.get("key_points", "").split("|")
            if point.strip()
        ] or document.analysis.key_points
        return ApiTranslation(
            title=fields.get("title", document.analysis.title),
            summary=summary,
            base_analysis={
                "summary": summary,
                "viewpoints": points,
                "quotes": [summary],
            },
        )

    def _offset(self, cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            offset = int(cursor)
        except ValueError as exc:
            raise ValueError("cursor must be a nonnegative integer") from exc
        if offset < 0:
            raise ValueError("cursor must be a nonnegative integer")
        return offset


def service_from_environment() -> ContentReadService:
    database_url = os.getenv("L2_DATABASE_URL")
    l1_database_url = os.getenv("L2_L1_DATABASE_URL")
    missing = [
        name
        for name, value in (
            ("L2_DATABASE_URL", database_url),
            ("L2_L1_DATABASE_URL", l1_database_url),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(f"missing required configuration: {', '.join(missing)}")
    assert database_url is not None
    assert l1_database_url is not None
    repository = SqlAlchemyJudgmentRepository(create_sqlalchemy_engine(database_url))
    provider = SqlAlchemyAnalysisProvider(create_l1_engine(l1_database_url))
    return ContentReadService(repository, provider)


def create_app(service: ContentReadService | None = None) -> FastAPI:
    app = FastAPI(title="CodePick L2 API")
    origins = [item.strip() for item in os.getenv("L2_CORS_ORIGINS", "").split(",") if item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type"],
        )
    runtime_service = service

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = os.getenv("L2_API_KEY")
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail={"code": "unauthorized"})

    def resolve_service() -> ContentReadService:
        nonlocal runtime_service
        if runtime_service is None:
            try:
                runtime_service = service_from_environment()
            except ConfigurationError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "configuration_error", "message": str(exc)},
                ) from exc
        return runtime_service

    def execute(call: Callable[[], T]) -> T:
        try:
            return call()
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "content_not_found", "message": str(exc)},
            ) from exc
        except UpstreamDataError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "upstream_data_error", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": str(exc)},
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "storage_unavailable", "message": "L2 storage unavailable"},
                headers={"Retry-After": "2"},
            ) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "l2-api"}

    @app.get("/content", dependencies=[Depends(authorize)])
    def list_content(
        vertical: str | None = None,
        status: str = "COMPLETED",
        cursor: str | None = None,
        limit: int = Query(default=20, ge=1, le=50),
        sort: str = "published_at",
    ) -> dict[str, Any]:
        current = resolve_service()
        page = execute(
            lambda: current.list_content(
                vertical=vertical,
                status=status,
                cursor=cursor,
                limit=limit,
                sort=sort,
            )
        )
        return page.model_dump(mode="json")

    @app.get("/content/{content_id}", dependencies=[Depends(authorize)])
    def get_content(content_id: int) -> dict[str, Any]:
        current = resolve_service()
        detail = execute(lambda: current.get_content(content_id))
        return detail.model_dump(mode="json")

    @app.get("/recommend", dependencies=[Depends(authorize)])
    def recommend_content(
        user_id: int,
        vertical: str | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        current = resolve_service()
        items = execute(lambda: current.recommend(user_id, vertical, limit))
        return {"items": [item.model_dump(mode="json") for item in items]}

    @app.get("/companion", dependencies=[Depends(authorize)])
    def companion_content(content_id: int, question: str = "") -> dict[str, list[str]]:
        current = resolve_service()
        return {"chunks": execute(lambda: current.companion(content_id, question))}

    return app


app = create_app()
