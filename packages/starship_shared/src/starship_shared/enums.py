from __future__ import annotations

from enum import StrEnum


class FactKind(StrEnum):
    MARKET_FACT = "MARKET_FACT"
    STRATEGY_SIGNAL = "STRATEGY_SIGNAL"
    ENGINE_EVENT = "ENGINE_EVENT"
