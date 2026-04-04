from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from starship_shared.schemas import EngineFactV1
from starship_shared.signing import sign_body_v1


class Publisher(Protocol):
    def publish(self, fact: EngineFactV1) -> None: ...


@dataclass
class FanoutPublisher:
    publishers: list[Publisher]

    def publish(self, fact: EngineFactV1) -> None:
        for pub in self.publishers:
            pub.publish(fact)


@dataclass
class HttpPublisher:
    url: str
    secret: str
    timeout_sec: float = 3.0

    def publish(self, fact: EngineFactV1) -> None:
        body = fact.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
        sig = sign_body_v1(self.secret, body)
        resp = requests.post(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Timestamp": sig.timestamp,
                "X-Engine-Signature": sig.signature,
            },
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()


@dataclass
class JsonlPublisher:
    path: str

    def publish(self, fact: EngineFactV1) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(fact.model_dump_json(exclude_none=True))
            f.write("\n")
