from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _candidate_env_files() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("STARSHIP_ENV_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / ".env")
    return candidates


def _apply_secret_file_overrides() -> Path | None:
    secret_dir_raw = os.environ.get("STARSHIP_SECRETS_DIR", "").strip()
    if not secret_dir_raw:
        return None
    secret_dir = Path(secret_dir_raw)
    if not secret_dir.exists() or not secret_dir.is_dir():
        return None
    for path in sorted(secret_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name.strip()
        if not name:
            continue
        os.environ[name] = path.read_text(encoding="utf-8").strip()
    return secret_dir


def _ensure_runtime_dir(env_name: str, default_path: str) -> Path:
    path = Path(os.environ.get(env_name, default_path)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    return _ensure_runtime_dir("STARSHIP_DATA_DIR", "data")


def get_logs_dir() -> Path:
    return _ensure_runtime_dir("STARSHIP_LOG_DIR", "logs")


def load_runtime_env() -> tuple[Path | None, Path | None]:
    dotenv_path: Path | None = None
    for candidate in _candidate_env_files():
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=True)
            dotenv_path = candidate
            break
    if dotenv_path is None:
        load_dotenv(override=True)
    secrets_dir = _apply_secret_file_overrides()
    return dotenv_path, secrets_dir
