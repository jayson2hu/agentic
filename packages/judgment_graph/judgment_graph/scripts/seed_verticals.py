from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine

from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository


def ai_coding_lens_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "verticals" / "ai-coding.json"


def load_ai_coding_seed() -> dict[str, object]:
    raw = json.loads(ai_coding_lens_path().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("ai-coding lens seed must be a JSON object")
    return cast(dict[str, object], raw)


def seed_ai_coding_lens(repository: SqlAlchemyJudgmentRepository) -> None:
    repository.seed_lens(load_ai_coding_seed())


def main() -> None:
    url = os.environ["L2_DATABASE_URL"]
    repository = SqlAlchemyJudgmentRepository(create_engine(url, future=True))
    seed_ai_coding_lens(repository)
    print("L2 VERTICAL SEED: PASS")


if __name__ == "__main__":
    main()
