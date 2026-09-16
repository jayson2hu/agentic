"""Validate the versioned, L1-owned SQL snapshot without importing upstream code."""

from __future__ import annotations

import math
from collections.abc import Mapping

from judgment_graph.contracts import BaseAnalysis

SNAPSHOT_COLUMNS = {
    "schema_version", "content_id", "input_snapshot", "analysis", "graph_version",
    "content_hash", "run_id", "status", "updated_at",
}


def positive_content_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("content_id must be a positive integer compatible with L2")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or text.startswith("0"):
        raise ValueError("content_id must be a canonical positive integer compatible with L2")
    number = int(text)
    if number > 2**63 - 1:
        raise ValueError("content_id exceeds the L2 BIGINT range")
    return number


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _string(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a nonempty string" if not allow_empty else f"{name} must be a string")
    return value


def _strings(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{name} must be a list of strings")
    return [_string(item, name) for item in value]


def analysis_from_snapshot(row: Mapping[str, object], requested_id: int) -> BaseAnalysis:
    version = row.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError(f"unsupported L1 snapshot schema_version: {version!r}")
    if row.get("status") != "WAIT_SCORE":
        raise ValueError("L1 snapshot is not ready: row status must be WAIT_SCORE")
    if positive_content_id(row.get("content_id")) != requested_id:
        raise ValueError("L1 snapshot content_id does not match requested content")
    for name in ("graph_version", "content_hash", "run_id"):
        _string(row.get(name), name)
    snapshot = _mapping(row.get("input_snapshot"), "input_snapshot")
    analysis = _mapping(row.get("analysis"), "analysis")
    for name, payload in (("input_snapshot", snapshot), ("analysis", analysis)):
        if positive_content_id(payload.get("content_id")) != requested_id:
            raise ValueError(f"{name}.content_id does not match requested content")
    if analysis.get("status") != "COMPLETED":
        raise ValueError("analysis.status must be COMPLETED")
    metadata = _mapping(snapshot.get("metadata"), "input_snapshot.metadata")
    source_metadata = metadata.get("source")
    source = source_metadata.get("name") if isinstance(source_metadata, Mapping) else None
    if not isinstance(source, str) or not source.strip():
        source = snapshot.get("source_url")
    language = analysis.get("lang")
    if language is None or language == "":
        language = metadata.get("lang")
    vector = analysis.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise ValueError("analysis.embedding must be a nonempty finite numeric vector")
    embedding: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("analysis.embedding must contain only finite numbers")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError("analysis.embedding must contain only finite numbers") from exc
        if not math.isfinite(number):
            raise ValueError("analysis.embedding must contain only finite numbers")
        embedding.append(number)
    exposure = metadata.get("exposure", 0)
    if isinstance(exposure, bool) or not isinstance(exposure, int) or exposure < 0:
        raise ValueError("input_snapshot.metadata.exposure must be a nonnegative integer")
    return BaseAnalysis(
        content_id=requested_id,
        title=_string(snapshot.get("title"), "input_snapshot.title", allow_empty=True),
        source=_string(source, "input_snapshot source name or source_url"),
        language=_string(language, "analysis.lang or input_snapshot.metadata.lang"),
        summary=_string(analysis.get("summary"), "analysis.summary"),
        key_points=_strings(analysis.get("key_points"), "analysis.key_points"),
        entities=_strings(analysis.get("entities"), "analysis.entities", allow_empty=True),
        tags=_strings(analysis.get("base_tags"), "analysis.base_tags"),
        embedding=embedding,
        text=_string(snapshot.get("body"), "input_snapshot.body"),
        exposure=exposure,
    )
