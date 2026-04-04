# src/bot/futures.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from tastytrade import Session

from starship_engine.apps.captain_log.logging import get_logger

log = get_logger("starship_engine.futures")


def get_future_product(session: Session, *, exchange: str, code: str) -> Optional[dict]:
    """
    Fetch a single future product definition from tastytrade.
    """
    try:
        if hasattr(session, "_get"):
            resp = session._get(f"/instruments/future-products/{exchange}/{code}")
        elif hasattr(session, "api") and hasattr(session.api, "get"):
            resp = session.api.get(f"/instruments/future-products/{exchange}/{code}")
        elif hasattr(session, "get"):
            resp = session.get(f"/instruments/future-products/{exchange}/{code}")
        else:
            log.warning("Session has no HTTP client; cannot resolve futures product.")
            return None
    except Exception as exc:
        log.warning("Futures product lookup failed: %s", exc)
        return None

    if hasattr(resp, "json"):
        try:
            resp = resp.json()
        except Exception:
            return None

    if not resp or not isinstance(resp, dict) or "data" not in resp:
        return None
    data = resp.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and "items" in data and data["items"]:
        return data["items"][0]
    return None


def _get_futures(
    session: Session,
    *,
    product_code: str,
) -> Optional[list[dict]]:
    def _extract_items(resp: dict) -> Optional[list[dict]]:
        if not resp or not isinstance(resp, dict):
            return None
        if "items" in resp and isinstance(resp.get("items"), list):
            return resp.get("items")
        if "data" not in resp:
            return None
        data = resp.get("data")
        if isinstance(data, dict) and "items" in data:
            return data.get("items")
        if isinstance(data, list):
            return data
        return None

    try:
        resp = session._get("/instruments/futures")
    except Exception as exc:
        log.warning("Futures list lookup failed (no params): %s", exc)
        return None

    # print(resp)
    items = _extract_items(resp)
    if not items:
        return None

    return [f for f in items if f.get("product-code") == product_code]


def resolve_active_future_streamer_symbol(
    session: Session,
    *,
    product_code: str,
) -> Optional[str]:
    futures = _get_futures(session, product_code=product_code)
    if not futures:
        return None

    active = [
        f for f in futures if f.get("active") is True and f.get("active-month") is True
    ]
    if not active:
        active = [f for f in futures if f.get("active") is True]
    if not active:
        return None

    def _exp_key(item: dict) -> tuple:
        exp = item.get("expiration-date")
        if isinstance(exp, str):
            try:
                dt = datetime.strptime(exp, "%Y-%m-%d")
                return (dt,)
            except Exception:
                pass
        return (datetime.max,)

    active.sort(key=_exp_key)
    symbol = active[0].get("streamer-symbol") or active[0].get("streamer_symbol")
    return symbol


def resolve_future_streamer_symbol(
    session: Session,
    *,
    exchange: str = "CME",
    code: str = "ES",
) -> Optional[str]:
    """
    Resolve a root streamer symbol for a future product, e.g. "/ES:XCME".
    """
    active = resolve_active_future_streamer_symbol(session, product_code=code)
    if active:
        return active

    data = get_future_product(session, exchange=exchange, code=code)
    if not data:
        return None

    root = data.get("root-symbol") or data.get("root_symbol")
    streamer_exch = data.get("streamer-exchange-code") or data.get(
        "streamer_exchange_code"
    )
    if not root:
        return None

    if streamer_exch:
        return f"{root}:{streamer_exch}"
    return root
