from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel


class EngineSettings(BaseModel):
    installed_apps: list[str] = []


@dataclass
class AppContext:
    settings: EngineSettings
    state: dict[str, Any]
    features: Optional[object] = None
    gate: Optional[object] = None
    event: Optional[dict[str, Any]] = None
