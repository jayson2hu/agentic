from __future__ import annotations

import json
from pathlib import Path

from judgment_graph.contracts import Lens


class FileLensLoader:
    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or Path(__file__).resolve().parents[1] / "config" / "verticals"

    def load_lens(self, vertical_code: str) -> Lens:
        path = self.config_dir / f"{vertical_code}.json"
        if not path.exists():
            raise KeyError(f"lens not found: {vertical_code}")
        lens = Lens(**json.loads(path.read_text(encoding="utf-8")))
        if not lens.enabled:
            raise ValueError(f"lens disabled: {vertical_code}")
        return lens


def load_lens(vertical_code: str) -> Lens:
    return FileLensLoader().load_lens(vertical_code)

