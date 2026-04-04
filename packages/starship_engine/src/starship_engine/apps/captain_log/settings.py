from __future__ import annotations

from pydantic import BaseModel


class CaptainLogSettings(BaseModel):
    enabled: bool = False
    out_dir: str = "./state_logs"
    fmt: str = "jsonl"
    every_bars: int = 1
