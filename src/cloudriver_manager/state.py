from __future__ import annotations

import json
from pathlib import Path

from .models import Checkpoint


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Checkpoint:
        if not self.path.exists():
            return Checkpoint()
        return Checkpoint.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, checkpoint: Checkpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

