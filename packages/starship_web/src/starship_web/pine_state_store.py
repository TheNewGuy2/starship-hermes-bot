from __future__ import annotations

import json
import re
from pathlib import Path

from starship_web.pine_models import PineTradeState
from starship_web.config import get_data_dir


def _states_dir() -> Path:
    path = get_data_dir() / "pine_bridge_states"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify_state_id(strategy_name: str, position_key: str) -> str:
    raw = f"{strategy_name}__{position_key}".strip().lower()
    clean = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    return clean or "pine-bridge-state"


def _state_path(state_id: str) -> Path:
    return _states_dir() / f"{state_id}.json"


def save_pine_state(state: PineTradeState) -> PineTradeState:
    _state_path(state.state_id).write_text(
        json.dumps(state.model_dump(), indent=2),
        encoding="utf-8",
    )
    return state


def load_pine_state(state_id: str) -> PineTradeState | None:
    path = _state_path(state_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return PineTradeState.model_validate(data)


def list_pine_states() -> list[PineTradeState]:
    states: list[PineTradeState] = []
    for path in sorted(
        _states_dir().glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            states.append(PineTradeState.model_validate(data))
        except Exception:
            continue
    return states


def delete_pine_state(state_id: str) -> bool:
    path = _state_path(state_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_pine_states() -> int:
    removed = 0
    for path in _states_dir().glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except Exception:
            continue
    return removed
