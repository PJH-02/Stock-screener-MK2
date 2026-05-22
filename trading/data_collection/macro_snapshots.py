"""Macro snapshot collection for point-in-time scoring."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading.config import DATA_CACHE_DIR
from trading.macro_dart_score import resolve_macro_root


class MacroSnapshotCollector:
    """Copy available macro snapshots into the trading data cache."""

    def __init__(self, *, macro_root: Path | None = None, cache_dir: Path = DATA_CACHE_DIR / "macro_snapshots") -> None:
        self.macro_root = macro_root or resolve_macro_root()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def collect(self, *, start_date: str, end_date: str) -> dict[str, Any]:
        if self.macro_root is None:
            return {"snapshots": 0, "source": None, "warning": "macro_root_missing"}

        source_dir = self.macro_root / "src" / "data" / "snapshots"
        if not source_dir.exists():
            return {"snapshots": 0, "source": str(source_dir), "warning": "macro_snapshot_dir_missing"}

        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        copied = 0
        for snapshot_path in sorted(source_dir.glob("*/snapshot.json")):
            published_at = self._published_date(snapshot_path)
            if published_at is None or published_at < start or published_at > end:
                continue
            target_dir = self.cache_dir / snapshot_path.parent.name
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_path, target_dir / "snapshot.json")
            copied += 1

        latest_path = source_dir / "latest.json"
        if latest_path.exists():
            shutil.copy2(latest_path, self.cache_dir / "latest.json")

        return {"snapshots": copied, "source": str(source_dir), "cache_dir": str(self.cache_dir)}

    @staticmethod
    def _published_date(snapshot_path: Path) -> date | None:
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            raw = payload.get("published_at") or payload.get("as_of_timestamp")
            if raw:
                return datetime.fromisoformat(str(raw)).date()
        except Exception:
            return None
        return None
