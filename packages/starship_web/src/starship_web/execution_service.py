from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from starship_shared.trade_ticket import TradeTicketV1
from starship_web.etrade_client import ETradeClient, load_client


def get_execution_mode() -> str:
    raw = os.environ.get("STARSHIP_EXECUTION_MODE", "dry_run").strip().lower()
    if raw not in {"disabled", "dry_run", "live"}:
        return "dry_run"
    return raw


def live_execution_enabled() -> bool:
    return os.environ.get("STARSHIP_ALLOW_LIVE_EXECUTION", "").strip().lower() == "true"


def _truncate_client_order_id(value: str) -> str:
    compact = "".join(ch for ch in value if ch.isalnum())
    if not compact:
        compact = "starshipexec"
    return compact[:20]


def build_place_order_request(ticket: TradeTicketV1) -> dict[str, Any]:
    preview_root = ticket.preview_request.get("PreviewOrderRequest", ticket.preview_request)
    if not isinstance(preview_root, dict) or not preview_root:
        raise RuntimeError(
            "Ticket does not have a valid preview request to convert into a place order request."
        )
    if not ticket.broker.preview_id:
        raise RuntimeError("Ticket does not have a broker preview ID yet.")
    orders = preview_root.get("Order")
    if not isinstance(orders, list) or not orders:
        raise RuntimeError("Preview request is missing Order details.")

    client_order_id = _truncate_client_order_id(
        str(
            preview_root.get("clientOrderId")
            or ticket.broker.client_order_id
            or f"{ticket.ticket_id}exec"
        )
    )
    order_type = str(preview_root.get("orderType") or ticket.broker.order_type).strip()
    if not order_type:
        raise RuntimeError("Preview request is missing order type.")

    return {
        "PlaceOrderRequest": {
            "orderType": order_type,
            "clientOrderId": client_order_id,
            "PreviewIds": [{"previewId": str(ticket.broker.preview_id)}],
            "Order": orders,
        }
    }


def submit_execution_package(
    ticket: TradeTicketV1,
    *,
    client: ETradeClient | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    mode = get_execution_mode()
    request_payload = build_place_order_request(ticket)

    if mode == "disabled":
        return {
            "ok": False,
            "mode": "disabled",
            "submitted": False,
            "timestamp": timestamp,
            "message": "Execution service is disabled. No broker call was made.",
            "request": request_payload,
        }

    if mode == "dry_run":
        return {
            "ok": True,
            "mode": "dry_run",
            "submitted": False,
            "timestamp": timestamp,
            "message": "Dry-run execution package built. No broker call was made.",
            "request": request_payload,
        }

    if not live_execution_enabled():
        return {
            "ok": False,
            "mode": "live_guarded",
            "submitted": False,
            "timestamp": timestamp,
            "message": (
                "Execution mode is set to live, but STARSHIP_ALLOW_LIVE_EXECUTION=true "
                "is not set. No broker call was made."
            ),
            "request": request_payload,
        }

    live_client = client or load_client()
    response = live_client.place_order(ticket.broker.account_id_key, request_payload)
    return {
        "ok": True,
        "mode": "live",
        "submitted": True,
        "timestamp": timestamp,
        "message": "Live order submitted to E*TRADE.",
        "request": request_payload,
        "response": response,
    }
