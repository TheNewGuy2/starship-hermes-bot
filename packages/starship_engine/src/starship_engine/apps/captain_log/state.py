# src/bot/state_log.py
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class StateLogConfig:
    enabled: bool
    out_dir: Path
    fmt: str = "jsonl"  # "jsonl" or "csv"


class StateLogger:
    def __init__(self, cfg: StateLogConfig, filename_base: str):
        self.cfg = cfg
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self.filename_base = filename_base
        self._date_suffix = datetime.now().strftime("%Y-%m-%d")
        self.path = self._build_path(self._date_suffix)
        self._csv_writer: Optional[csv.DictWriter] = None
        self._csv_file = None

    def _build_path(self, date_suffix: str) -> Path:
        return self.cfg.out_dir / f"{self.filename_base}_{date_suffix}.{self.cfg.fmt}"

    def _maybe_rollover(self) -> None:
        current = datetime.now().strftime("%Y-%m-%d")
        if current == self._date_suffix:
            return
        self._date_suffix = current
        self.path = self._build_path(current)
        if self._csv_file:
            try:
                self._csv_file.close()
            except Exception:
                pass
        self._csv_file = None
        self._csv_writer = None

    def close(self) -> None:
        if self._csv_file:
            try:
                self._csv_file.close()
            except Exception:
                pass

    def write(self, row: dict[str, Any]) -> None:
        if not self.cfg.enabled:
            return
        self._maybe_rollover()

        if self.cfg.fmt == "jsonl":
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
                f.flush()
            return

        # CSV
        if self._csv_writer is None:
            self._csv_file = self.path.open("a", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(
                self._csv_file, fieldnames=list(row.keys())
            )
            if self._csv_file.tell() == 0:
                self._csv_writer.writeheader()

        self._csv_writer.writerow(row)
        self._csv_file.flush()
