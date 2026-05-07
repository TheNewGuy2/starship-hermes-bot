from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Any

from starship_shared.trade_ticket import (
    TradeTicketBrokerRef,
    TradeTicketExecutionIntent,
    TradeTicketLeg,
    TradeTicketPricing,
    TradeTicketV1,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _new_ticket_id() -> str:
    return f"tt-{uuid.uuid4().hex[:12]}"


def _friendly_status_reason_from_error(error_message: str) -> str:
    lower = error_message.lower()
    if '"code":100' in lower or "service is not currently available" in lower:
        return (
            "E*TRADE sandbox preview service is temporarily unavailable. "
            "Your ticket is still valid; retry later."
        )
    if '"code":2040' in lower or "price you specified for this order is invalid" in lower:
        return (
            "E*TRADE rejected the order price increment. Rebuild the preview request "
            "to snap the net price to a valid option increment, then retry."
        )
    if '"code":4' in lower or "price type you specified" in lower:
        return (
            "E*TRADE rejected the order format. The ticket needs payload adjustment "
            "before preview can succeed."
        )
    return "Preview failed. Review the broker response and try again."


def _next_retry_at_from_error(error_message: str) -> str | None:
    lower = error_message.lower()
    if '"code":100' in lower or "service is not currently available" in lower:
        return (_utc_now() + timedelta(minutes=5)).isoformat()
    return None


def _infer_order_type(strategy: str | None, legs_count: int) -> str:
    normalized = (strategy or "").strip().lower()
    if normalized == "condor" and legs_count == 4:
        return "IRON_CONDOR"
    if legs_count > 1:
        return "SPREADS"
    return "OPTN"


def _leg_role(leg: TradeTicketLeg) -> tuple[str, str] | None:
    cp = (leg.call_put or "").strip().upper()
    action = (leg.order_action or "").strip().upper()
    if cp not in {"PUT", "CALL"}:
        return None
    if action in {"SELL_OPEN", "BUY_CLOSE", "SELL_TO_OPEN", "BUY_TO_CLOSE"}:
        return cp, "SHORT"
    if action in {"BUY_OPEN", "SELL_CLOSE", "BUY_TO_OPEN", "SELL_TO_CLOSE"}:
        return cp, "LONG"
    return None


def _resolve_spread_order_type(ticket: TradeTicketV1) -> str:
    fallback = ticket.broker.order_type or _infer_order_type(ticket.strategy, len(ticket.legs))
    if len(ticket.legs) != 4:
        return fallback

    by_role: dict[tuple[str, str], TradeTicketLeg] = {}
    for leg in ticket.legs:
        role = _leg_role(leg)
        if role is None:
            return fallback
        by_role[role] = leg

    required_roles = {
        ("PUT", "SHORT"),
        ("PUT", "LONG"),
        ("CALL", "SHORT"),
        ("CALL", "LONG"),
    }
    if set(by_role) != required_roles:
        return fallback

    short_put = by_role[("PUT", "SHORT")].strike_price
    long_put = by_role[("PUT", "LONG")].strike_price
    short_call = by_role[("CALL", "SHORT")].strike_price
    long_call = by_role[("CALL", "LONG")].strike_price
    if None in {short_put, long_put, short_call, long_call}:
        return fallback

    # Canonical condor geometry:
    # long_put < short_put < short_call < long_call
    if not (long_put < short_put < short_call < long_call):
        return "SPREADS"

    put_width = round(short_put - long_put, 6)
    call_width = round(long_call - short_call, 6)
    if put_width <= 0 or call_width <= 0:
        return "SPREADS"

    if abs(put_width - call_width) < 1e-6:
        return "IRON_CONDOR"
    return "SPREADS"


def _price_increment_for_ticket(ticket: TradeTicketV1, *, price_type: str) -> float:
    normalized_price_type = (price_type or "").strip().upper()
    if normalized_price_type not in {"NET_CREDIT", "NET_DEBIT"}:
        return 0.01

    roots: set[str] = set()
    if ticket.underlier:
        roots.add(ticket.underlier.strip().upper())
    for leg in ticket.legs:
        if leg.symbol:
            roots.add(leg.symbol.strip().upper())
        option_root = str(leg.broker_payload.get("option_root_symbol", "")).strip().upper()
        if option_root:
            roots.add(option_root)

    order_type = (ticket.broker.order_type or "").strip().upper()
    if roots.intersection({"SPX", "SPXW"}) and order_type in {"IRON_CONDOR", "SPREADS"}:
        return 0.05
    return 0.01


def normalize_preview_limit_price(
    ticket: TradeTicketV1,
    *,
    price_type: str,
    limit_price: float,
) -> tuple[float, str | None]:
    increment = _price_increment_for_ticket(ticket, price_type=price_type)
    raw = Decimal(str(limit_price))
    step = Decimal(str(increment))
    snapped = (raw / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
    if raw > 0:
        snapped = max(step, snapped)
    normalized = float(snapped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if abs(normalized - limit_price) < 1e-9:
        return normalized, None

    if increment == 0.05:
        warning = (
            f"Preview limit price adjusted from {limit_price:.4f} to {normalized:.2f} "
            "to satisfy SPX/SPXW complex-order $0.05 net price increments."
        )
    else:
        warning = (
            f"Preview limit price adjusted from {limit_price:.4f} to {normalized:.2f} "
            f"to satisfy broker price increments of ${increment:.2f}."
        )
    return normalized, warning


def _broker_symbol_candidates(underlier: str) -> list[str]:
    normalized = underlier.strip().upper()
    aliases = {
        "SPX": ["SPX", "SPXW", "XSP"],
        "SPXW": ["SPX", "SPXW", "XSP"],
        "XSP": ["XSP", "SPXW", "SPX"],
    }
    return aliases.get(normalized, [normalized])


def _next_trading_day_utc() -> tuple[int, int, int]:
    now = datetime.now(timezone.utc).date()
    weekday = now.weekday()
    if weekday >= 4:
        days_forward = 7 - weekday
    else:
        days_forward = 1
    nxt = now + timedelta(days=days_forward)
    return nxt.year, nxt.month, nxt.day


def build_trade_ticket_from_preview(
    *,
    account_id_key: str,
    preview_order_request: dict[str, Any],
    preview_response: dict[str, Any],
    strategy: str | None = None,
    source: str = "manual",
) -> TradeTicketV1:
    response_root = preview_response.get("PreviewOrderResponse", preview_response)
    request_root = preview_order_request.get("PreviewOrderRequest", preview_order_request)
    request_orders = _ensure_list(
        request_root.get("Order") if isinstance(request_root, dict) else []
    )
    request_order = (
        request_orders[0]
        if request_orders and isinstance(request_orders[0], dict)
        else {}
    )
    order = response_root.get("Order", {}) if isinstance(response_root, dict) else {}
    if not isinstance(order, dict) or not order:
        order = request_order
    instruments = _ensure_list(order.get("Instrument") if isinstance(order, dict) else [])
    if not instruments and isinstance(request_order, dict):
        instruments = _ensure_list(request_order.get("Instrument"))

    warnings: list[str] = []
    messages = order.get("messages", {}) if isinstance(order, dict) else {}
    for message in _ensure_list(
        messages.get("Message") if isinstance(messages, dict) else []
    ):
        if isinstance(message, dict):
            desc = message.get("description")
            if desc:
                warnings.append(str(desc))

    legs: list[TradeTicketLeg] = []
    underlier = ""
    for index, instrument in enumerate(instruments, start=1):
        if not isinstance(instrument, dict):
            continue
        product = (
            instrument.get("Product", {})
            if isinstance(instrument.get("Product"), dict)
            else {}
        )
        symbol = str(product.get("symbol", ""))
        if not underlier:
            underlier = symbol
        legs.append(
            TradeTicketLeg(
                leg_id=f"leg-{index}",
                symbol=symbol,
                security_type=str(product.get("securityType", "")),
                order_action=str(instrument.get("orderAction", "")),
                quantity=_as_float(
                    instrument.get("orderedQuantity", instrument.get("quantity"))
                )
                or 0.0,
                quantity_type=str(instrument.get("quantityType", "QUANTITY")),
                call_put=(
                    str(product.get("callPut"))
                    if product.get("callPut") is not None
                    else None
                ),
                expiry_year=(
                    int(product["expiryYear"])
                    if product.get("expiryYear") is not None
                    else None
                ),
                expiry_month=(
                    int(product["expiryMonth"])
                    if product.get("expiryMonth") is not None
                    else None
                ),
                expiry_day=(
                    int(product["expiryDay"])
                    if product.get("expiryDay") is not None
                    else None
                ),
                strike_price=_as_float(product.get("strikePrice")),
                osi_key=(
                    str(instrument.get("osiKey"))
                    if instrument.get("osiKey") is not None
                    else None
                ),
                broker_payload=instrument,
            )
        )

    preview_ids = _ensure_list(
        response_root.get("PreviewIds") if isinstance(response_root, dict) else []
    )
    preview_id = None
    if preview_ids and isinstance(preview_ids[0], dict):
        preview_id = str(preview_ids[0].get("previewId", "")) or None

    pricing = TradeTicketPricing(
        price_type=str(order.get("priceType", request_order.get("priceType", ""))),
        limit_price=_as_float(order.get("limitPrice", request_order.get("limitPrice"))),
        stop_price=_as_float(order.get("stopPrice", request_order.get("stopPrice"))),
        estimated_total_amount=_as_float(order.get("estimatedTotalAmount")),
        estimated_commission=_as_float(order.get("estimatedCommission")),
        net_price=_as_float(order.get("netPrice")),
        net_bid=_as_float(order.get("netBid")),
        net_ask=_as_float(order.get("netAsk")),
    )

    status = "previewed" if preview_id else ("preview_failed" if warnings else "draft")
    now = _utc_now_iso()

    return TradeTicketV1(
        ticket_id=_new_ticket_id(),
        created_at=now,
        updated_at=now,
        status=status,
        source="signal" if source == "signal" else "manual",
        strategy=strategy,
        underlier=underlier or "UNKNOWN",
        broker=TradeTicketBrokerRef(
            provider="etrade",
            account_id_key=account_id_key,
            preview_id=preview_id,
            order_type=str(
                response_root.get("orderType", request_root.get("orderType", ""))
            ),
            client_order_id=(
                str(request_root.get("clientOrderId"))
                if request_root.get("clientOrderId") is not None
                else None
            ),
        ),
        pricing=pricing,
        legs=legs,
        preview_attempts=1 if preview_id or warnings else 0,
        last_preview_at=now if preview_id or warnings else None,
        last_error=(warnings[0] if warnings else None),
        status_reason=(_friendly_status_reason_from_error(warnings[0]) if warnings else None),
        next_retry_at=(_next_retry_at_from_error(warnings[0]) if warnings else None),
        warnings=warnings,
        preview_response=preview_response,
        preview_request=preview_order_request,
    )


def build_trade_ticket_from_signal(
    *,
    fact: dict[str, Any],
    account_id_key: str,
    client_order_id: str | None = None,
) -> TradeTicketV1:
    signal = fact.get("signal", {}) if isinstance(fact, dict) else {}
    market = fact.get("market", {}) if isinstance(fact, dict) else {}
    underlier = str(market.get("underlier", "SPX"))
    broker_symbol = _broker_symbol_candidates(underlier)[0]
    expiry_year, expiry_month, expiry_day = _next_trading_day_utc()
    strategy = str(fact.get("strategy", "signal"))
    now = _utc_now_iso()

    def _leg(
        index: int,
        *,
        strike_key: str,
        action: str,
    ) -> TradeTicketLeg:
        strike = _as_float(signal.get(strike_key))
        return TradeTicketLeg(
            leg_id=f"leg-{index}",
            symbol=broker_symbol,
            security_type="OPTN",
            order_action=action,
            quantity=1.0,
            quantity_type="QUANTITY",
            expiry_year=expiry_year,
            expiry_month=expiry_month,
            expiry_day=expiry_day,
            strike_price=strike,
            broker_payload={
                "source_strike_key": strike_key,
                "original_underlier": underlier,
                "broker_symbol_candidates": _broker_symbol_candidates(underlier),
            },
        )

    legs = [
        _leg(1, strike_key="short_put", action="SELL_OPEN"),
        _leg(2, strike_key="long_put", action="BUY_OPEN"),
        _leg(3, strike_key="short_call", action="SELL_OPEN"),
        _leg(4, strike_key="long_call", action="BUY_OPEN"),
    ]

    warnings = [
        "Signal-derived ticket created before broker preview.",
        f"Default broker root set to {broker_symbol}.",
        "Option expiry and live contract identifiers still need broker enrichment.",
    ]
    order_type = _infer_order_type(strategy, len(legs))

    return TradeTicketV1(
        ticket_id=_new_ticket_id(),
        created_at=now,
        updated_at=now,
        status="draft",
        source="signal",
        strategy=strategy,
        underlier=underlier,
        broker=TradeTicketBrokerRef(
            provider="etrade",
            account_id_key=account_id_key,
            preview_id=None,
            order_type=order_type,
            client_order_id=client_order_id,
        ),
        pricing=TradeTicketPricing(
            price_type="NET_CREDIT",
            limit_price=None,
            stop_price=None,
        ),
        legs=legs,
        warnings=warnings,
        preview_response={},
        preview_request={},
    )


def prepare_ticket_for_preview(
    ticket: TradeTicketV1,
    *,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    quantity: int,
    limit_price: float,
    client_order_id: str | None = None,
    price_type: str = "NET_CREDIT",
    note: str = "Prepared from ticket detail form.",
) -> TradeTicketV1:
    now = _utc_now_iso()
    normalized_limit_price, increment_warning = normalize_preview_limit_price(
        ticket,
        price_type=price_type,
        limit_price=limit_price,
    )
    prepared_legs: list[TradeTicketLeg] = []
    instruments: list[dict[str, Any]] = []
    order_type = _resolve_spread_order_type(ticket)

    for index, leg in enumerate(ticket.legs, start=1):
        call_put = leg.call_put
        source_strike_key = str(leg.broker_payload.get("source_strike_key", ""))
        if not call_put:
            if "put" in source_strike_key or index <= 2:
                call_put = "PUT"
            else:
                call_put = "CALL"

        product_symbol = str(
            leg.broker_payload.get("option_root_symbol")
            or leg.symbol
        ).strip()

        prepared_leg = leg.model_copy(
            update={
                "quantity": float(quantity),
                "call_put": call_put,
                "expiry_year": expiry_year,
                "expiry_month": expiry_month,
                "expiry_day": expiry_day,
            }
        )
        prepared_legs.append(prepared_leg)
        instruments.append(
            {
                "Product": {
                    "symbol": product_symbol,
                    "securityType": prepared_leg.security_type,
                    "callPut": prepared_leg.call_put,
                    "expiryYear": prepared_leg.expiry_year,
                    "expiryMonth": prepared_leg.expiry_month,
                    "expiryDay": prepared_leg.expiry_day,
                    "strikePrice": prepared_leg.strike_price,
                },
                "orderAction": prepared_leg.order_action,
                "quantityType": prepared_leg.quantity_type,
                "quantity": quantity,
                "orderedQuantity": quantity,
            }
        )

    if not client_order_id:
        client_order_id = ticket.broker.client_order_id or f"{ticket.ticket_id}-preview"

    preview_request = {
        "PreviewOrderRequest": {
            "orderType": order_type,
            "clientOrderId": client_order_id,
            "Order": [
                {
                    "allOrNone": False,
                    "priceType": price_type,
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "limitPrice": normalized_limit_price,
                    "stopPrice": 0,
                    "Instrument": instruments,
                }
            ],
        }
    }

    warnings = [
        warning
        for warning in ticket.warnings
        if "broker enrichment" not in warning.lower()
        and not warning.startswith("Preview limit price adjusted from ")
    ]
    if increment_warning:
        warnings.append(increment_warning)

    notes = [note]
    if increment_warning:
        notes.append(increment_warning)
    notes.append("Not executed yet.")

    return ticket.model_copy(
        update={
            "updated_at": now,
            "status": "ready",
            "legs": prepared_legs,
            "pricing": ticket.pricing.model_copy(
                update={
                    "price_type": price_type,
                    "limit_price": normalized_limit_price,
                    "stop_price": 0.0,
                }
            ),
            "broker": ticket.broker.model_copy(
                update={
                    "client_order_id": client_order_id,
                    "order_type": order_type,
                }
            ),
            "execution_intent": TradeTicketExecutionIntent(
                quantity=quantity,
                price_type=price_type,
                limit_price=normalized_limit_price,
                stop_price=0.0,
                order_term="GOOD_FOR_DAY",
                market_session="REGULAR",
                all_or_none=False,
                user_confirmed=False,
                ready_for_execution=True,
                prepared_at=now,
                notes=notes,
            ),
            "preview_request": preview_request,
            "last_error": None,
            "status_reason": "Preview request prepared. Retry preview when ready.",
            "next_retry_at": None,
            "warnings": warnings,
        }
    )


def rebuild_ticket_preview_request(ticket: TradeTicketV1) -> TradeTicketV1:
    first_leg = ticket.legs[0] if ticket.legs else None
    expiry_year = (
        ticket.execution_intent.prepared_at and first_leg and first_leg.expiry_year
    ) or (first_leg.expiry_year if first_leg and first_leg.expiry_year else datetime.now(timezone.utc).year)
    expiry_month = first_leg.expiry_month if first_leg and first_leg.expiry_month else datetime.now(timezone.utc).month
    expiry_day = first_leg.expiry_day if first_leg and first_leg.expiry_day else datetime.now(timezone.utc).day
    quantity = int(
        ticket.execution_intent.quantity
        or (first_leg.quantity if first_leg and first_leg.quantity else 1)
        or 1
    )
    limit_price = (
        ticket.execution_intent.limit_price
        or ticket.pricing.limit_price
        or ticket.pricing.suggested_limit_price
        or 1.00
    )
    client_order_id = ticket.broker.client_order_id or f"{ticket.ticket_id}-preview"
    return prepare_ticket_for_preview(
        ticket,
        expiry_year=int(expiry_year),
        expiry_month=int(expiry_month),
        expiry_day=int(expiry_day),
        quantity=quantity,
        limit_price=float(limit_price),
        client_order_id=client_order_id,
        price_type=(
            ticket.execution_intent.price_type
            or ticket.pricing.price_type
            or "NET_CREDIT"
        ),
    )


def mark_ticket_preview_pending(ticket: TradeTicketV1) -> TradeTicketV1:
    now = _utc_now_iso()
    return ticket.model_copy(
        update={
            "status": "preview_pending",
            "updated_at": now,
            "preview_attempts": ticket.preview_attempts + 1,
            "last_preview_at": now,
            "last_error": None,
            "status_reason": "Submitting preview request to E*TRADE.",
        }
    )


def mark_ticket_preview_failed(
    ticket: TradeTicketV1, *, error_message: str, preview_response: dict[str, Any] | None = None
) -> TradeTicketV1:
    now = _utc_now_iso()
    status_reason = _friendly_status_reason_from_error(error_message)
    next_retry_at = _next_retry_at_from_error(error_message)
    warnings = [w for w in ticket.warnings if w != error_message]
    warnings.append(error_message)
    update: dict[str, Any] = {
        "status": "preview_failed",
        "updated_at": now,
        "last_preview_at": now,
        "last_error": error_message,
        "status_reason": status_reason,
        "next_retry_at": next_retry_at,
        "warnings": warnings,
    }
    if preview_response is not None:
        update["preview_response"] = preview_response
    return ticket.model_copy(update=update)


def mark_ticket_previewed(
    ticket: TradeTicketV1,
    *,
    preview_response: dict[str, Any],
) -> TradeTicketV1:
    fresh = build_trade_ticket_from_preview(
        account_id_key=ticket.broker.account_id_key,
        preview_order_request=ticket.preview_request,
        preview_response=preview_response,
        strategy=ticket.strategy,
        source=ticket.source,
    )
    merged_warnings = list(ticket.warnings)
    for warning in fresh.warnings:
        if warning not in merged_warnings:
            merged_warnings.append(warning)
    return fresh.model_copy(
        update={
            "ticket_id": ticket.ticket_id,
            "created_at": ticket.created_at,
            "updated_at": _utc_now_iso(),
            "preview_attempts": ticket.preview_attempts,
            "status_reason": "Preview succeeded. Review response and confirm intent before execution.",
            "next_retry_at": None,
            "warnings": merged_warnings[-25:],
        }
    )
