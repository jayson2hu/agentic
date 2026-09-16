from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, MetaData, Table, create_engine, inspect, literal, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import ColumnElement

from judgment_graph.contracts import BaseAnalysis
from judgment_graph.input.provider import ContentDocument
from judgment_graph.input.snapshot_contract import (
    SNAPSHOT_COLUMNS,
    analysis_from_snapshot,
    positive_content_id,
)


def create_l1_engine(url: str) -> Engine:
    return create_engine(url, future=True)


class SqlAlchemyAnalysisProvider:
    """Read-only adapter from L1 SQL tables to the L2 BaseAnalysis contract."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.metadata = MetaData()
        self.content_base_analysis = Table(
            "content_base_analysis", self.metadata, autoload_with=engine
        )
        columns = set(self.content_base_analysis.c.keys())
        self.versioned_snapshot = bool({"schema_version", "input_snapshot"} & columns)
        self.content_items: Table | None = None
        self.processing_runs: Table | None = None
        if self.versioned_snapshot:
            missing = SNAPSHOT_COLUMNS - columns
            if missing:
                raise ValueError(f"incomplete L1 snapshot table contract: {sorted(missing)}")
            if inspect(engine).has_table("l1_processing_runs"):
                self.processing_runs = Table(
                    "l1_processing_runs", self.metadata, autoload_with=engine
                )
        else:
            # Legacy hand-built schemas still need the historical L0/L1 table join.
            self.content_items = Table("content_items", self.metadata, autoload_with=engine)

    def get(self, content_id: int) -> BaseAnalysis:
        if self.versioned_snapshot:
            requested_id = positive_content_id(content_id)
            snapshot_row = self._versioned_row(requested_id)
            if snapshot_row is None:
                raise KeyError(f"L1 base_analysis not found: {requested_id}")
            return analysis_from_snapshot(dict(snapshot_row), requested_id)
        assert self.content_items is not None
        item_id_col = self._required_col(self.content_items, "id")
        analysis_content_col = self._first_col(
            self.content_base_analysis, ("content_id", "item_id", "id")
        )
        if analysis_content_col is None:
            raise RuntimeError("content_base_analysis requires content_id, item_id, or id")

        query = (
            select(
                item_id_col.label("content_id"),
                self._col(self.content_items, ("title",), "title"),
                self._col(self.content_items, ("source", "source_name", "url"), "source"),
                self._col(self.content_items, ("language", "lang"), "language"),
                self._col(self.content_items, ("text", "body", "content"), "text"),
                self._col(self.content_items, ("exposure", "views", "score"), "exposure"),
                self._col(
                    self.content_base_analysis,
                    ("summary", "abstract"),
                    "summary",
                ),
                self._col(self.content_base_analysis, ("key_points", "highlights"), "key_points"),
                self._col(self.content_base_analysis, ("entities",), "entities"),
                self._col(self.content_base_analysis, ("tags", "topics"), "tags"),
                self._col(self.content_base_analysis, ("embedding", "vector"), "embedding"),
                self._col(self.content_base_analysis, ("fields", "analysis", "payload"), "fields"),
                analysis_content_col.label("analysis_content_id"),
            )
            .select_from(
                self.content_items.outerjoin(
                    self.content_base_analysis,
                    item_id_col == analysis_content_col,
                )
            )
            .where(item_id_col == content_id)
        )

        with self.engine.connect() as conn:
            row = conn.execute(query).mappings().first()
        if row is None:
            raise KeyError(f"L1 base_analysis not found: {content_id}")
        if row["analysis_content_id"] is None:
            raise KeyError(f"L1 base_analysis not found: {content_id}")
        return self._base_analysis_from_row(row)

    def get_document(self, content_id: int) -> ContentDocument:
        requested_id = positive_content_id(content_id)
        if not self.versioned_snapshot:
            raise RuntimeError("L2 HTTP requires the versioned L1 snapshot contract")
        row = self._versioned_row(requested_id)
        if row is None:
            raise KeyError(f"L1 base_analysis not found: {requested_id}")
        analysis = analysis_from_snapshot(dict(row), requested_id)
        snapshot = row["input_snapshot"]
        analysis_payload = row["analysis"]
        if not isinstance(snapshot, Mapping) or not isinstance(analysis_payload, Mapping):
            raise TypeError("L1 snapshot payloads must be JSON objects")
        source_url = snapshot.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError("input_snapshot.source_url is required by the L2 HTTP contract")
        published_at = self._published_at(snapshot.get("published_at"), row["updated_at"])
        metadata = snapshot.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        thumbnail_value = metadata.get("thumbnail") or metadata.get("image_url")
        thumbnail = str(thumbnail_value) if thumbnail_value else None
        quotes_value = analysis_payload.get("quotes", [])
        quotes = [str(item) for item in quotes_value] if isinstance(quotes_value, list) else []
        return ContentDocument(
            analysis=analysis,
            url=source_url,
            published_at=published_at,
            quotes=quotes,
            thumbnail=thumbnail,
        )

    def get_for_run(self, content_id: int, run_id: str) -> BaseAnalysis:
        requested_id = positive_content_id(content_id)
        if not run_id.strip():
            raise ValueError("run_id is required")
        if self.processing_runs is None:
            raise RuntimeError("L1 processing run history is unavailable")
        query = select(self.processing_runs).where(
            self.processing_runs.c.content_id == str(requested_id),
            self.processing_runs.c.run_id == run_id,
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).mappings().one_or_none()
        if row is None:
            raise KeyError(f"L1 processing run not found: {requested_id}/{run_id}")
        snapshot = {
            "schema_version": 1,
            "content_id": row["content_id"],
            "input_snapshot": row["input_snapshot"],
            "analysis": row["analysis"],
            "graph_version": row["graph_version"],
            "content_hash": row["content_hash"],
            "run_id": row["run_id"],
            "status": row["status"],
            "updated_at": row["finished_at"],
        }
        return analysis_from_snapshot(snapshot, requested_id)

    def _versioned_row(self, content_id: int) -> RowMapping | None:
        query = select(self.content_base_analysis).where(
            self.content_base_analysis.c.content_id == str(content_id)
        )
        with self.engine.connect() as conn:
            return conn.execute(query).mappings().one_or_none()

    def _published_at(self, value: object, fallback: object) -> datetime:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str) and value.strip():
            result = datetime.fromisoformat(value)
        elif isinstance(fallback, datetime):
            result = fallback
        else:
            raise ValueError("L1 snapshot published_at and updated_at are unavailable")
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result

    def _base_analysis_from_row(self, row: RowMapping) -> BaseAnalysis:
        fields = self._dict_value(row["fields"])
        return BaseAnalysis(
            content_id=int(row["content_id"]),
            title=self._string_value(self._pick(row, fields, "title"), default=""),
            source=self._string_value(self._pick(row, fields, "source"), default="unknown"),
            language=self._string_value(self._pick(row, fields, "language"), default="unknown"),
            summary=self._string_value(self._pick(row, fields, "summary"), default=""),
            key_points=self._string_list(self._pick(row, fields, "key_points")),
            entities=self._string_list(self._pick(row, fields, "entities")),
            tags=self._string_list(self._pick(row, fields, "tags")),
            embedding=self._float_list(self._pick(row, fields, "embedding")),
            text=self._string_value(self._pick(row, fields, "text"), default=""),
            exposure=self._int_value(self._pick(row, fields, "exposure")),
        )

    def _pick(self, row: RowMapping, fields: Mapping[str, object], key: str) -> object:
        value = row.get(key)
        if value is not None:
            return value
        return fields.get(key)

    def _required_col(self, table: Table, name: str) -> ColumnElement[Any]:
        if name not in table.c:
            raise RuntimeError(f"{table.name} requires {name}")
        return table.c[name]

    def _first_col(self, table: Table, names: Sequence[str]) -> ColumnElement[Any] | None:
        for name in names:
            if name in table.c:
                return table.c[name]
        return None

    def _col(self, table: Table, names: Sequence[str], label: str) -> ColumnElement[Any]:
        col = self._first_col(table, names)
        if col is None:
            return literal(None).label(label)
        return col.label(label)

    def _dict_value(self, value: object) -> dict[str, object]:
        parsed = self._json_value(value)
        if isinstance(parsed, Mapping):
            return {str(key): item for key, item in parsed.items()}
        return {}

    def _string_value(self, value: object, default: str) -> str:
        if value is None:
            return default
        return str(value)

    def _int_value(self, value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, int | float | str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    def _string_list(self, value: object) -> list[str]:
        parsed = self._json_value(value)
        if parsed is None:
            return []
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, Iterable) and not isinstance(parsed, Mapping):
            return [str(item) for item in parsed]
        return []

    def _float_list(self, value: object) -> list[float]:
        parsed = self._json_value(value)
        if isinstance(parsed, Iterable) and not isinstance(parsed, (str, bytes, Mapping)):
            result: list[float] = []
            for item in parsed:
                if not isinstance(item, int | float | str):
                    continue
                try:
                    result.append(float(item))
                except ValueError:
                    continue
            return result
        return []

    def _json_value(self, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
