from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from starship_shared.schemas import CandleBarV1, OptionSnapshotV1


def _append_jsonl(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


class _DedupeWriter:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def dedupe_append(self, path: Path, key: str, line: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        _append_jsonl(path, line)
        return True


def _daily_path(out_dir: Path, prefix: str, ts: datetime) -> Path:
    date_suffix = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return out_dir / f"{prefix}_{date_suffix}.jsonl"


class CandleStore:
    def __init__(self, *, out_dir: Path, prefix: str) -> None:
        self.out_dir = out_dir
        self.prefix = prefix
        self._writer = _DedupeWriter()

    def write_candle(self, candle: CandleBarV1) -> bool:
        path = _daily_path(self.out_dir, self.prefix, candle.ts)
        key = f"{candle.symbol}|{candle.timeframe}|{candle.ts.isoformat()}"
        line = candle.model_dump_json(exclude_none=True)
        return self._writer.dedupe_append(path, key, line)


class OptionsStore:
    def __init__(self, *, out_dir: Path, prefix: str) -> None:
        self.out_dir = out_dir
        self.prefix = prefix
        self._writer = _DedupeWriter()

    def write_option_snapshot(self, snap: OptionSnapshotV1) -> bool:
        path = _daily_path(self.out_dir, self.prefix, snap.ts)
        key = snap.ts.isoformat()
        line = snap.model_dump_json(exclude_none=True)
        return self._writer.dedupe_append(path, key, line)
