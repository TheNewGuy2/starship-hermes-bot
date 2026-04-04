from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class App(Protocol):
    name: str
    settings: BaseModel
    logger: logging.Logger

    def on_start(self, ctx: "AppContext") -> None: ...
    def on_candle(self, ctx: "AppContext") -> None: ...
    def on_signal(self, ctx: "AppContext") -> None: ...
    def on_event(self, ctx: "AppContext") -> None: ...
    def on_stop(self, ctx: "AppContext") -> None: ...


@dataclass(frozen=True)
class AppSpec:
    module: str


def load_apps(apps: Iterable[str]) -> list[App]:
    loaded: list[App] = []
    for module_path in apps:
        mod = importlib.import_module(module_path)
        if hasattr(mod, "app"):
            app = getattr(mod, "app")
        else:
            mod = importlib.import_module(f"{module_path}.app")
            if not hasattr(mod, "app"):
                raise ImportError(f"App module {module_path} has no 'app' attribute")
            app = getattr(mod, "app")
        loaded.append(app)
    return loaded
