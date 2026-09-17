from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError

from judgment_graph.contracts import Translation, VerticalScore
from judgment_graph.heuristic import HeuristicProvider
from judgment_graph.input.provider import ContentDocument, ContentDocumentProvider
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider, create_l1_engine
from judgment_graph.llm import FakeLLM, LLMClient, create_llm
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import (
    SqlAlchemyJudgmentRepository,
    create_sqlalchemy_engine,
)

T = TypeVar("T")


class ConfigurationError(RuntimeError):
    pass


class UpstreamDataError(RuntimeError):
    pass


class ContentVersionChanged(RuntimeError):
    """The accepted content version changed while constructing a response."""


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
    published_at: datetime | None
    thumbnail: str | None = None
    summary: str
    scores: dict[str, int] = Field(default_factory=dict)
    language: str = "unknown"
    reading_minutes: int = Field(default=1, ge=1)
    tags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


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
        query: str | None = None,
    ) -> ContentPage:
        if status != "COMPLETED":
            return ContentPage(items=[], total=0)
        offset = self._offset(cursor)
        scores = self._best_scores(vertical)
        details = [self._detail(score.content_id, score) for score in scores]
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            details = [
                item
                for item in details
                if normalized_query in item.title.casefold()
                or normalized_query in item.summary.casefold()
            ]
        if sort == "score":
            details.sort(key=lambda item: (-item.scores.get("quality", 0), int(item.id)))
        elif sort == "published_at":
            details.sort(key=lambda item: (item.published_at or datetime.min.replace(tzinfo=UTC), int(item.id)), reverse=True)
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
        return self._detail(content_id, None)

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
        detail, version = self._read_detail(content_id, None)
        llm = HeuristicProvider() if detail.provenance.get("scoring_method") == "heuristic" else self.llm
        chunks = llm.stream_text("companion", {
            "title": detail.title, "summary": detail.summary,
            "key_points": detail.base_analysis["viewpoints"], "question": question,
        })
        result = [chunk.removeprefix("data: ").strip() for chunk in chunks if chunk.strip()]
        self._assert_current(content_id, version)
        return result

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

    def _source_version(self, content_id: int) -> tuple[str, int] | None:
        try:
            return self.repository.get_source_version(content_id)
        except (ValueError, TypeError) as exc:
            raise UpstreamDataError(f"L2 accepted source identity is invalid: {content_id}") from exc

    def _assert_current(self, content_id: int, version: tuple[str, int] | None) -> None:
        if (
            self._source_version(content_id) != version
            or self.repository.get_status(content_id) != "COMPLETED"
        ):
            raise ContentVersionChanged(f"content changed while being read: {content_id}")

    def _detail(self, content_id: int, candidate: VerticalScore | None) -> ContentDetail:
        detail, _version = self._read_detail(content_id, candidate)
        return detail

    def _read_detail(
        self, content_id: int, candidate: VerticalScore | None
    ) -> tuple[ContentDetail, tuple[str, int] | None]:
        # Capture the accepted identity before reading any authoritative product.
        # A prefetched list/recommendation score is only a candidate, never the
        # score paired directly with a later source-version lookup.
        version = self._source_version(content_id)
        if self.repository.get_status(content_id) != "COMPLETED":
            if candidate is not None or self._source_version(content_id) != version:
                raise ContentVersionChanged(f"content changed while being read: {content_id}")
            raise KeyError(content_id)
        self._assert_current(content_id, version)
        try:
            vertical = candidate.vertical_code if candidate is not None else None
            rows = [
                item for item in self.repository.completed_scores(vertical)
                if item.content_id == content_id
            ]
            if not rows:
                if candidate is not None:
                    raise ContentVersionChanged(f"candidate changed while being read: {content_id}")
                raise UpstreamDataError(f"completed L2 content has no score: {content_id}")
            score = max(rows, key=lambda item: (item.quality_score, item.vertical_code))
            if candidate is not None and candidate != score:
                raise ContentVersionChanged(f"candidate changed while being read: {content_id}")
            document = (
                self.provider.get_document_for_run(content_id, version[0])
                if version is not None else self.provider.get_document(content_id)
            )
            translations = self._translations(content_id, document)
            detail = self._build_detail(content_id, score, document, translations)
        except ContentVersionChanged:
            raise
        except (KeyError, ValueError, TypeError, RuntimeError) as exc:
            # A missing snapshot/product during a version transition is retryable,
            # not a permanent upstream-data error or a content-not-found response.
            self._assert_current(content_id, version)
            raise UpstreamDataError(f"L1/L2 products are missing or invalid: {content_id}") from exc
        self._assert_current(content_id, version)
        return detail, version

    @staticmethod
    def _scoring_method(model: str) -> str:
        normalized = model.strip().casefold()
        if re.fullmatch(r"heuristic-v\d+(?:[.-]\d+)*", normalized):
            return "heuristic"
        if re.search(r"(?:^|[^a-z])fake(?:[^a-z]|$)|fakellm", normalized):
            return "simulated"
        # A model-name string alone does not establish a verified model provider.
        return "unknown"

    def _build_detail(
        self,
        content_id: int,
        score: VerticalScore,
        document: ContentDocument,
        translations: dict[str, ApiTranslation],
    ) -> ContentDetail:
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
            language=document.analysis.language,
            reading_minutes=max(1, math.ceil(len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", document.analysis.text)) / 220)),
            tags=document.analysis.tags,
            provenance={
                **document.provenance,
                "source_url": document.url,
                "scoring_method": self._scoring_method(score.model),
                "model": score.model,
                "reviewed": score.reviewed,
                "translation_available": bool(translations),
            },
            base_analysis={
                "summary": document.analysis.summary,
                "viewpoints": document.analysis.key_points,
                "quotes": document.quotes,
            },
            translations=translations,
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
                "quotes": [],
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


def _validate_l2_read_schema(engine: Engine) -> None:
    """Check HTTP read dependencies without creating or upgrading any tables."""
    required = {
        models.content_judgment_state.name: {
            "content_id", "status", "source_run_id", "source_revision",
        },
        models.content_vertical_scores.name: set(models.content_vertical_scores.c.keys()),
        models.content_translations.name: set(models.content_translations.c.keys()),
    }
    missing: list[str] = []
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in required.items():
            if table not in tables:
                missing.append(f"missing table {table}")
                continue
            actual = {column["name"] for column in inspector.get_columns(table)}
            absent = columns - actual
            if absent:
                missing.append(f"{table} missing columns {', '.join(sorted(absent))}")
    if missing:
        raise ConfigurationError(
            "L2 HTTP schema is incomplete; apply the project migrations before serving: "
            + "; ".join(missing)
        )


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
    _validate_l2_read_schema(repository.engine)
    provider = SqlAlchemyAnalysisProvider(create_l1_engine(l1_database_url))
    try:
        llm = create_llm()
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    return ContentReadService(repository, provider, llm)


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
            except (ConfigurationError, ValueError, NoSuchTableError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "configuration_error", "message": str(exc),
                        "retryable": False,
                    },
                ) from exc
            except SQLAlchemyError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "storage_unavailable", "message": "L2 storage unavailable",
                        "retryable": True,
                    },
                    headers={"Retry-After": "2"},
                ) from exc
        return runtime_service

    def execute(call: Callable[[], T]) -> T:
        try:
            return call()
        except ContentVersionChanged as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "content_version_changed",
                    "message": str(exc),
                    "retryable": True,
                },
                headers={"Retry-After": "1"},
            ) from exc
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

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/docs", status_code=307)

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
        q: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        current = resolve_service()
        page = execute(
            lambda: current.list_content(
                vertical=vertical,
                status=status,
                cursor=cursor,
                limit=limit,
                sort=sort,
                query=q,
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
