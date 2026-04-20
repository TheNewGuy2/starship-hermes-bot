from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BrokerProvider(StrEnum):
    TASTYTRADE = "tastytrade"
    ETRADE = "etrade"


@dataclass(frozen=True)
class BrokerSessionBundle:
    provider: BrokerProvider
    session: Any
