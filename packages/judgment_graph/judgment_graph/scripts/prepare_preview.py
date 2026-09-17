"""Process a local L1 SQLite snapshot using the normal L2 graph and offline rules."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from judgment_graph.graph.build import run_content_pipeline
from judgment_graph.heuristic import HeuristicProvider
from judgment_graph.input.sqlalchemy_provider import SqlAlchemyAnalysisProvider, create_l1_engine
from judgment_graph.lens.loader import FileLensLoader
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import (
    SqlAlchemyJudgmentRepository,
    create_sqlalchemy_engine,
)


def prepare(l1_path: Path, l2_path: Path) -> dict[str, object]:
    if not l1_path.is_file():
        raise ValueError("L1 database must already exist")
    if l1_path.resolve() == l2_path.resolve():
        raise ValueError("L1 and L2 must use different databases")
    provider = SqlAlchemyAnalysisProvider(create_l1_engine(f"sqlite:///{l1_path.resolve()}"))
    repository = SqlAlchemyJudgmentRepository(create_sqlalchemy_engine(f"sqlite:///{l2_path.resolve()}"))
    repository.create_schema()
    with repository.engine.connect() as connection:
        incompatible = connection.execute(
            select(models.content_vertical_scores.c.content_id).where(
                models.content_vertical_scores.c.model != "heuristic-v1"
            ).limit(1)
        ).first()
        translations = connection.execute(select(models.content_translations).limit(1)).first()
    if incompatible or translations:
        raise ValueError("Use a separate L2 preview database: existing results are not heuristic-only")
    table = provider.content_base_analysis
    with provider.engine.connect() as connection:
        snapshots = connection.execute(select(table).where(table.c.status == "WAIT_SCORE")).mappings().all()
    outcomes = []
    for row in snapshots:
        content_id = int(row["content_id"])
        run_content_pipeline(
            content_id, "ai-coding", provider, FileLensLoader(), HeuristicProvider(), repository,
            source_run_id=row["run_id"], source_revision=int(row["revision"]),
        )
        outcomes.append({"content_id": content_id, "status": repository.get_status(content_id)})
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "processing": "heuristic-v1", "translations": "unavailable",
        "completed": sum(item["status"] == "COMPLETED" for item in outcomes),
        "items": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l1-db", type=Path, required=True)
    parser.add_argument("--l2-db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.l1_db, args.l2_db)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["completed"]:
        raise SystemExit("No completed content: inspect review/relevance outcomes")


if __name__ == "__main__":
    main()
