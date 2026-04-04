from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SigHeaders:
    timestamp: str
    signature: str


def sign_body_v1(secret: str, body: bytes, *, now: int | None = None) -> SigHeaders:
    ts = str(int(time.time() if now is None else now))
    base = b"v1:" + ts.encode() + b":" + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return SigHeaders(timestamp=ts, signature="v1=" + digest)


def verify_body_v1(
    secret: str,
    body: bytes,
    timestamp: str,
    signature: str,
    *,
    max_age_sec: int = 300,
) -> bool:
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - ts_int) > max_age_sec:
        return False

    base = b"v1:" + timestamp.encode() + b":" + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    expected = "v1=" + digest
    return hmac.compare_digest(expected, signature)
