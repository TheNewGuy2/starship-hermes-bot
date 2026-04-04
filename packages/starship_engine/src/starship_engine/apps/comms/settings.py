from __future__ import annotations

from pydantic import BaseModel


class CommsSettings(BaseModel):
    engine_ingest_url: str = "http://127.0.0.1:8000/engine/ingest"
    engine_ingest_secret: str = ""
    engine_facts_jsonl: str = "data/facts.jsonl"
    engine_run_id: str = ""
