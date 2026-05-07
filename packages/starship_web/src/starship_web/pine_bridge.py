from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from starship_shared.trade_ticket import (
    TradeTicketBrokerRef,
    TradeTicketPricing,
    TradeTicketV1,
)
from starship_web.etrade_client import ETradeClient, load_client
from starship_web.pine_models import PineAlertEvent, PineTradeState, parse_pine_alert_payload
from starship_web.pine_state_store import load_pine_state, save_pine_state, slugify_state_id
from starship_web.ticket_store import load_ticket, save_ticket
from starship_web.trade_tickets import (
    build_trade_ticket_from_signal,
    mark_ticket_preview_failed,
    mark_ticket_preview_pending,
    mark_ticket_previewed,
    normalize_preview_limit_price,
    prepare_ticket_for_preview,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_tradingview_webhook_secret() -> str:
    return os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "").strip()


def _point_value() -> float:
    raw = os.environ.get("PINE_BRIDGE_POINT_VALUE", "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 100.0
    return value if value > 0 else 100.0


def _compact_client_order_id(prefix: str, event_id: str) -> str:
    compact = "".join(ch for ch in f"{prefix}{event_id}" if ch.isalnum())
    if not compact:
        compact = prefix
    return compact[:20]


def _as_float(value: object) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _ensure_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _parse_bar_date(event: PineAlertEvent) -> tuple[int, int, int] | None:
    if event.expiry_year and event.expiry_month and event.expiry_day:
        return event.expiry_year, event.expiry_month, event.expiry_day
    if not event.bar_time:
        return None
    raw = event.bar_time.strip()
    if raw.isdigit():
        try:
            value = int(raw)
            # Pine commonly emits Unix milliseconds for time/time_close.
            ts = value / 1000.0 if value >= 10_000_000_000 else float(value)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.year, dt.month, dt.day
        except Exception:
            return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    return dt.year, dt.month, dt.day


def _candidate_symbols(underlier: str, option_root: str | None) -> list[str]:
    normalized = underlier.strip().upper()
    aliases = {
        "SPX": ["SPX", "SPXW", "XSP"],
        "SPXW": ["SPX", "SPXW", "XSP"],
        "XSP": ["XSP", "SPX", "SPXW"],
    }
    out: list[str] = []
    for item in aliases.get(normalized, [normalized]):
        if item not in out:
            out.append(item)
    if option_root:
        root = option_root.strip().upper()
        if root and root not in out:
            out.append(root)
    return out


def _infer_leg_call_put(ticket: TradeTicketV1, leg_index: int) -> str:
    leg = ticket.legs[leg_index]
    if leg.call_put in ("PUT", "CALL"):
        return leg.call_put
    source_strike_key = str(leg.broker_payload.get("source_strike_key", ""))
    if "put" in source_strike_key or leg_index <= 1:
        return "PUT"
    return "CALL"


def _extract_expiry_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    root = payload.get("OptionExpireDateResponse", {}) if isinstance(payload, dict) else {}
    rows = root.get("ExpirationDate", []) if isinstance(root, dict) else []
    if isinstance(rows, dict):
        rows = [rows]
    return [row for row in rows if isinstance(row, dict)]


def _selected_expiry_from_chain(chain_payload: dict[str, object]) -> tuple[int, int, int] | None:
    root = chain_payload.get("OptionChainResponse", {}) if isinstance(chain_payload, dict) else {}
    selected = root.get("SelectedED", {}) if isinstance(root, dict) else {}
    if not isinstance(selected, dict):
        return None
    try:
        return int(selected["year"]), int(selected["month"]), int(selected["day"])
    except Exception:
        return None


def _find_matching_contracts(ticket: TradeTicketV1, chain_payload: dict[str, object]) -> list[dict[str, object]] | None:
    root = chain_payload.get("OptionChainResponse", {}) if isinstance(chain_payload, dict) else {}
    pairs = root.get("OptionPair", []) if isinstance(root, dict) else []
    if isinstance(pairs, dict):
        pairs = [pairs]

    matches: list[dict[str, object]] = []
    for index, leg in enumerate(ticket.legs):
        target_strike = leg.strike_price
        target_cp = _infer_leg_call_put(ticket, index)
        matched = None
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            branch = pair.get("Put" if target_cp == "PUT" else "Call")
            if not isinstance(branch, dict):
                continue
            strike = _as_float(branch.get("strikePrice"))
            if strike is None or target_strike is None:
                continue
            if abs(strike - target_strike) < 1e-6:
                matched = branch
                break
        if matched is None:
            return None
        matches.append(matched)
    return matches


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value + 1e-9, 2)


def _derive_entry_credit_suggestion(ticket: TradeTicketV1, matches: list[dict[str, object]]) -> dict[str, float | None]:
    net_mid = 0.0
    natural_credit = 0.0
    for index, leg in enumerate(ticket.legs):
        match = matches[index]
        bid = _as_float(match.get("bid"))
        ask = _as_float(match.get("ask"))
        if bid is None or ask is None:
            return {
                "suggested_limit_price": None,
                "suggested_net_mid": None,
                "suggested_natural_credit": None,
            }
        mid = (bid + ask) / 2.0
        if "SELL" in leg.order_action:
            net_mid += mid
            natural_credit += bid
        else:
            net_mid -= mid
            natural_credit -= ask

    suggested_mid_raw = _round_price(max(net_mid, 0.01))
    suggested_mid, _ = normalize_preview_limit_price(
        ticket,
        price_type="NET_CREDIT",
        limit_price=float(suggested_mid_raw or 0.01),
    )
    suggested_natural = _round_price(max(natural_credit, 0.01))
    return {
        "suggested_limit_price": suggested_mid,
        "suggested_net_mid": _round_price(net_mid),
        "suggested_natural_credit": _round_price(natural_credit),
    }


def _build_market_snapshot(
    ticket: TradeTicketV1,
    *,
    matched_symbol: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    matches: list[dict[str, object]],
) -> dict[str, object]:
    legs: list[dict[str, object]] = []
    for index, leg in enumerate(ticket.legs):
        match = matches[index]
        legs.append(
            {
                "leg_id": leg.leg_id,
                "symbol": matched_symbol,
                "call_put": _infer_leg_call_put(ticket, index),
                "strike_price": leg.strike_price,
                "osi_key": match.get("osiKey"),
                "bid": _as_float(match.get("bid")),
                "ask": _as_float(match.get("ask")),
                "last_price": _as_float(match.get("lastPrice")),
                "volume": match.get("volume"),
                "open_interest": match.get("openInterest"),
                "quote_timestamp": match.get("timeStamp"),
            }
        )
    return {
        "underlier": ticket.underlier,
        "matched_symbol": matched_symbol,
        "expiry": {
            "year": expiry_year,
            "month": expiry_month,
            "day": expiry_day,
        },
        "captured_at": _utc_now_iso(),
        "legs": legs,
    }


def _entry_reference_credit(ticket: TradeTicketV1, fallback_credit: float | None = None) -> float | None:
    return (
        ticket.execution_intent.limit_price
        or ticket.pricing.limit_price
        or ticket.pricing.net_price
        or ticket.pricing.suggested_limit_price
        or fallback_credit
    )


def _derive_live_mark_summary(
    ticket: TradeTicketV1,
    *,
    matched_symbol: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    matches: list[dict[str, object]],
    fallback_entry_credit: float | None = None,
) -> dict[str, object]:
    open_hint = _derive_entry_credit_suggestion(ticket, matches)
    close_mid_debit = 0.0
    close_natural_debit = 0.0
    legs: list[dict[str, object]] = []
    for index, leg in enumerate(ticket.legs):
        match = matches[index]
        bid = _as_float(match.get("bid"))
        ask = _as_float(match.get("ask"))
        if bid is None or ask is None:
            raise RuntimeError("Matched broker quote is missing bid/ask.")
        mid = (bid + ask) / 2.0
        if "SELL" in leg.order_action:
            close_mid_debit += mid
            close_natural_debit += ask
        else:
            close_mid_debit -= mid
            close_natural_debit -= bid
        legs.append(
            {
                "leg_id": leg.leg_id,
                "symbol": matched_symbol,
                "call_put": _infer_leg_call_put(ticket, index),
                "strike_price": leg.strike_price,
                "osi_key": match.get("osiKey"),
                "bid": bid,
                "ask": ask,
                "mid": _round_price(mid),
                "last_price": _as_float(match.get("lastPrice")),
                "quote_timestamp": match.get("timeStamp"),
            }
        )

    quantity = int(ticket.execution_intent.quantity or (ticket.legs[0].quantity if ticket.legs else 1) or 1)
    entry_credit_ref = _entry_reference_credit(ticket, fallback_entry_credit)
    pnl_mid = None
    pnl_natural = None
    if entry_credit_ref is not None:
        pnl_mid = entry_credit_ref - close_mid_debit
        pnl_natural = entry_credit_ref - close_natural_debit

    point_value = _point_value()
    return {
        "captured_at": _utc_now_iso(),
        "matched_symbol": matched_symbol,
        "expiry": {
            "year": expiry_year,
            "month": expiry_month,
            "day": expiry_day,
        },
        "quantity": quantity,
        "entry_credit_reference": _round_price(entry_credit_ref),
        "current_open_mid_credit": open_hint.get("suggested_net_mid"),
        "current_open_natural_credit": open_hint.get("suggested_natural_credit"),
        "close_mid_debit": _round_price(close_mid_debit),
        "close_natural_debit": _round_price(close_natural_debit),
        "pnl_mid_points": _round_price(pnl_mid),
        "pnl_natural_points": _round_price(pnl_natural),
        "pnl_mid_usd": (_round_price(pnl_mid * point_value * quantity) if pnl_mid is not None else None),
        "pnl_natural_usd": (
            _round_price(pnl_natural * point_value * quantity) if pnl_natural is not None else None
        ),
        "legs": legs,
    }


def _preferred_expiry_rows(
    expiry_rows: list[dict[str, object]],
    preferred_expiry: tuple[int, int, int] | None,
) -> list[dict[str, object]]:
    if preferred_expiry is None:
        return expiry_rows
    preferred: list[dict[str, object]] = []
    rest: list[dict[str, object]] = []
    for row in expiry_rows:
        row_key = (
            int(row.get("year", 0)),
            int(row.get("month", 0)),
            int(row.get("day", 0)),
        )
        if row_key == preferred_expiry:
            preferred.append(row)
        else:
            rest.append(row)
    return preferred + rest


def _apply_contract_matches(
    ticket: TradeTicketV1,
    *,
    matched_symbol: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    matches: list[dict[str, object]],
) -> TradeTicketV1:
    pricing_hints = _derive_entry_credit_suggestion(ticket, matches)
    market_snapshot = _build_market_snapshot(
        ticket,
        matched_symbol=matched_symbol,
        expiry_year=expiry_year,
        expiry_month=expiry_month,
        expiry_day=expiry_day,
        matches=matches,
    )
    legs = []
    for index, leg in enumerate(ticket.legs):
        match = matches[index]
        merged_payload = dict(leg.broker_payload)
        merged_payload["contract_match"] = match
        if match.get("optionRootSymbol") is not None:
            merged_payload["option_root_symbol"] = str(match.get("optionRootSymbol"))
        merged_payload["chain_lookup_symbol"] = matched_symbol
        legs.append(
            leg.model_copy(
                update={
                    "symbol": matched_symbol,
                    "call_put": _infer_leg_call_put(ticket, index),
                    "expiry_year": expiry_year,
                    "expiry_month": expiry_month,
                    "expiry_day": expiry_day,
                    "osi_key": (
                        str(match.get("osiKey"))
                        if match.get("osiKey") is not None
                        else leg.osi_key
                    ),
                    "broker_payload": merged_payload,
                }
            )
        )
    return ticket.model_copy(
        update={
            "updated_at": _utc_now_iso(),
            "legs": legs,
            "pricing": ticket.pricing.model_copy(update=pricing_hints),
            "market_snapshot": market_snapshot,
        }
    )


def _build_entry_ticket(event: PineAlertEvent, account_id_key: str) -> TradeTicketV1:
    fact = {
        "strategy": event.strategy_name,
        "market": {"underlier": event.underlier},
        "signal": {
            "short_put": event.short_put,
            "long_put": event.long_put,
            "short_call": event.short_call,
            "long_call": event.long_call,
        },
    }
    client_order_id = _compact_client_order_id("pine", event.event_id)
    ticket = build_trade_ticket_from_signal(
        fact=fact,
        account_id_key=account_id_key,
        client_order_id=client_order_id,
    )
    candidates = _candidate_symbols(event.underlier, event.option_root)
    legs = []
    for leg in ticket.legs:
        payload = dict(leg.broker_payload)
        payload["broker_symbol_candidates"] = candidates
        payload["pine_event_id"] = event.event_id
        legs.append(leg.model_copy(update={"broker_payload": payload}))
    warnings = [
        "TradingView Pine bridge entry alert received.",
        f"Pine event id: {event.event_id}",
    ]
    return ticket.model_copy(
        update={
            "strategy": event.strategy_name,
            "legs": legs,
            "broker": ticket.broker.model_copy(update={"order_type": "IRON_CONDOR"}),
            "pricing": ticket.pricing.model_copy(
                update={
                    "limit_price": event.target_entry_credit,
                }
            ),
            "warnings": warnings,
            "broker_suggestions": {"pine_payload": event.raw_payload},
        }
    )


def _match_ticket_for_entry(
    ticket: TradeTicketV1,
    *,
    event: PineAlertEvent,
    client: ETradeClient,
) -> tuple[TradeTicketV1, dict[str, object]]:
    symbols = _candidate_symbols(event.underlier, event.option_root)
    preferred_expiry = _parse_bar_date(event)
    attempts: list[dict[str, object]] = []
    for symbol in symbols:
        try:
            expiries = client.get_option_expire_dates(symbol, expiry_type="ALL")
        except Exception as exc:
            attempts.append(
                {
                    "symbol": symbol,
                    "matched": False,
                    "error": str(exc),
                }
            )
            continue
        expiry_rows = _preferred_expiry_rows(_extract_expiry_rows(expiries), preferred_expiry)
        for row in expiry_rows[:6]:
            year = int(row["year"])
            month = int(row["month"])
            day = int(row["day"])
            try:
                chain = client.get_option_chain(
                    symbol,
                    expiry_year=year,
                    expiry_month=month,
                    expiry_day=day,
                    include_weekly=True,
                    price_type="ALL",
                )
            except Exception as exc:
                attempts.append(
                    {
                        "symbol": symbol,
                        "expiry": {"year": year, "month": month, "day": day},
                        "matched": False,
                        "error": str(exc),
                    }
                )
                continue
            matches = _find_matching_contracts(ticket, chain)
            attempts.append({"symbol": symbol, "expiry": {"year": year, "month": month, "day": day}, "matched": matches is not None})
            if matches is None:
                continue
            selected_expiry = _selected_expiry_from_chain(chain) or (year, month, day)
            enriched = _apply_contract_matches(
                ticket,
                matched_symbol=symbol,
                expiry_year=selected_expiry[0],
                expiry_month=selected_expiry[1],
                expiry_day=selected_expiry[2],
                matches=matches,
            )
            return enriched, {
                "selected_symbol": symbol,
                "selected_expiry": {
                    "year": selected_expiry[0],
                    "month": selected_expiry[1],
                    "day": selected_expiry[2],
                },
                "attempts": attempts,
                "matched_contracts": matches,
            }
    raise RuntimeError(f"Unable to match Pine strikes to live E*TRADE contracts. Attempts={len(attempts)}")


def _refresh_live_mark(
    ticket: TradeTicketV1,
    *,
    client: ETradeClient,
    fallback_entry_credit: float | None = None,
) -> dict[str, object]:
    if not ticket.legs:
        raise RuntimeError("Ticket has no legs to mark.")
    first_leg = ticket.legs[0]
    if not first_leg.expiry_year or not first_leg.expiry_month or not first_leg.expiry_day:
        raise RuntimeError("Ticket legs are missing expiry data.")
    symbol = first_leg.symbol
    chain = client.get_option_chain(
        symbol,
        expiry_year=first_leg.expiry_year,
        expiry_month=first_leg.expiry_month,
        expiry_day=first_leg.expiry_day,
        include_weekly=True,
        price_type="ALL",
    )
    matches = _find_matching_contracts(ticket, chain)
    if matches is None:
        raise RuntimeError("Current live chain did not contain all matched ticket strikes.")
    return _derive_live_mark_summary(
        ticket,
        matched_symbol=symbol,
        expiry_year=first_leg.expiry_year,
        expiry_month=first_leg.expiry_month,
        expiry_day=first_leg.expiry_day,
        matches=matches,
        fallback_entry_credit=fallback_entry_credit,
    )


def _new_ticket_id() -> str:
    return f"tt-{uuid.uuid4().hex[:12]}"


def _reverse_order_action(action: str) -> str:
    mapping = {
        "SELL_OPEN": "BUY_CLOSE",
        "BUY_OPEN": "SELL_CLOSE",
        "SELL_TO_OPEN": "BUY_TO_CLOSE",
        "BUY_TO_OPEN": "SELL_TO_CLOSE",
    }
    return mapping.get(action, action)


def _build_close_ticket(
    entry_ticket: TradeTicketV1,
    event: PineAlertEvent,
    *,
    live_mark: dict[str, object],
) -> TradeTicketV1:
    now = _utc_now_iso()
    legs = [
        leg.model_copy(update={"order_action": _reverse_order_action(leg.order_action)})
        for leg in entry_ticket.legs
    ]
    return TradeTicketV1(
        ticket_id=_new_ticket_id(),
        created_at=now,
        updated_at=now,
        status="draft",
        source="signal",
        strategy=f"{event.strategy_name}:close",
        underlier=entry_ticket.underlier,
        broker=TradeTicketBrokerRef(
            provider="etrade",
            account_id_key=entry_ticket.broker.account_id_key,
            preview_id=None,
            order_type=entry_ticket.broker.order_type,
            client_order_id=_compact_client_order_id("close", event.event_id),
        ),
        pricing=TradeTicketPricing(
            price_type="NET_DEBIT",
        ),
        legs=legs,
        warnings=[
            f"TradingView Pine bridge close alert received for entry ticket {entry_ticket.ticket_id}.",
        ],
        broker_suggestions={"parent_entry_ticket_id": entry_ticket.ticket_id, "pine_payload": event.raw_payload},
        market_snapshot=dict(live_mark),
    )


def _apply_state_event(
    state: PineTradeState,
    *,
    event: PineAlertEvent,
    status: str,
    active: bool,
    warnings: list[str] | None = None,
    entry_ticket_id: str | None = None,
    close_ticket_id: str | None = None,
    latest_live_mark: dict[str, object] | None = None,
    clear_close_ticket: bool = False,
    clear_closed_at: bool = False,
) -> PineTradeState:
    now = _utc_now_iso()
    event_log = list(state.event_log)[-19:]
    event_log.append(
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "bar_time": event.bar_time,
            "processed_at": now,
        }
    )
    processed = [item for item in state.processed_event_ids if item != event.event_id]
    processed = (processed + [event.event_id])[-50:]
    merged_warnings = list(state.warnings)
    if warnings:
        merged_warnings.extend(warnings)
    return state.model_copy(
        update={
            "strategy_name": event.strategy_name,
            "position_key": event.position_key,
            "underlier": event.underlier,
            "option_root": event.option_root or state.option_root,
            "status": status,
            "active": active,
            "quantity": event.quantity or state.quantity,
            "entry_ticket_id": entry_ticket_id or state.entry_ticket_id,
            "close_ticket_id": (
                None if clear_close_ticket else (close_ticket_id or state.close_ticket_id)
            ),
            "updated_at": now,
            "opened_at": (
                now
                if active and (not state.active or not state.opened_at)
                else state.opened_at
            ),
            "closed_at": (
                None
                if clear_closed_at
                else (now if not active and event.event_type == "CLOSE" else state.closed_at)
            ),
            "last_event_id": event.event_id,
            "last_event_type": event.event_type,
            "last_event_at": event.bar_time or now,
            "current_strikes": {
                "short_put": event.short_put or state.current_strikes.get("short_put"),
                "long_put": event.long_put or state.current_strikes.get("long_put"),
                "short_call": event.short_call or state.current_strikes.get("short_call"),
                "long_call": event.long_call or state.current_strikes.get("long_call"),
            },
            "latest_aux": dict(event.aux),
            "latest_live_mark": latest_live_mark or state.latest_live_mark,
            "latest_pine_payload": dict(event.raw_payload),
            "processed_event_ids": processed,
            "event_log": event_log,
            "warnings": merged_warnings[-25:],
        }
    )


def process_pine_alert(
    payload: dict[str, object],
    *,
    client: ETradeClient | None = None,
) -> dict[str, object]:
    configured_secret = get_tradingview_webhook_secret()
    payload_secret = str(payload.get("secret", "")).strip()
    if configured_secret and payload_secret != configured_secret:
        raise RuntimeError("TradingView webhook secret mismatch.")

    event = parse_pine_alert_payload(payload)
    state_id = slugify_state_id(event.strategy_name, event.position_key)
    state = load_pine_state(state_id) or PineTradeState(
        state_id=state_id,
        strategy_name=event.strategy_name,
        position_key=event.position_key,
        underlier=event.underlier,
        option_root=event.option_root,
    )
    if event.event_id in state.processed_event_ids:
        return {
            "ok": True,
            "duplicate": True,
            "state": state.model_dump(),
        }

    live_client = client or load_client()
    account_id_key = os.environ.get("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "").strip()
    if not account_id_key:
        raise RuntimeError("ETRADE_DEFAULT_ACCOUNT_ID_KEY is not configured.")

    if event.event_type == "ENTRY":
        if state.active:
            raise RuntimeError(
                f"State {state.state_id} is already active with entry ticket {state.entry_ticket_id}."
            )
        if None in (event.short_put, event.long_put, event.short_call, event.long_call):
            raise RuntimeError("ENTRY alert is missing one or more strike fields.")
        ticket = _build_entry_ticket(event, account_id_key)
        ticket, match_payload = _match_ticket_for_entry(ticket, event=event, client=live_client)
        entry_limit = event.target_entry_credit or ticket.pricing.suggested_limit_price
        if entry_limit is None:
            raise RuntimeError("Matched contracts did not produce a suggested entry credit.")
        first_leg = ticket.legs[0]
        prepared = prepare_ticket_for_preview(
            ticket,
            expiry_year=int(first_leg.expiry_year or 0),
            expiry_month=int(first_leg.expiry_month or 0),
            expiry_day=int(first_leg.expiry_day or 0),
            quantity=event.quantity,
            limit_price=float(entry_limit),
            client_order_id=ticket.broker.client_order_id,
            price_type="NET_CREDIT",
            note="Prepared automatically from Pine bridge entry alert.",
        )
        save_ticket(prepared)
        pending = mark_ticket_preview_pending(prepared)
        save_ticket(pending)
        try:
            preview = live_client.preview_order(
                pending.broker.account_id_key,
                pending.preview_request,
            )
            final_ticket = mark_ticket_previewed(pending, preview_response=preview)
        except Exception as exc:
            final_ticket = mark_ticket_preview_failed(pending, error_message=str(exc))
            save_ticket(final_ticket)
            state = _apply_state_event(
                state,
                event=event,
                status="error",
                active=False,
                warnings=[str(exc)],
                entry_ticket_id=final_ticket.ticket_id,
                clear_close_ticket=True,
                clear_closed_at=True,
            )
            save_pine_state(state)
            return {
                "ok": False,
                "duplicate": False,
                "state": state.model_dump(),
                "ticket": final_ticket.model_dump(),
                "match": match_payload,
            }
        save_ticket(final_ticket)
        try:
            live_mark = _refresh_live_mark(
                final_ticket,
                client=live_client,
                fallback_entry_credit=event.target_entry_credit,
            )
            entry_warnings: list[str] = []
        except Exception as exc:
            live_mark = {
                "captured_at": _utc_now_iso(),
                "error": str(exc),
                "entry_credit_reference": _entry_reference_credit(
                    final_ticket,
                    event.target_entry_credit,
                ),
                "market_snapshot": final_ticket.market_snapshot,
            }
            entry_warnings = [f"Initial live mark refresh failed: {exc}"]
        state = _apply_state_event(
            state,
            event=event,
            status="open",
            active=True,
            entry_ticket_id=final_ticket.ticket_id,
            latest_live_mark=live_mark,
            warnings=entry_warnings,
            clear_close_ticket=True,
            clear_closed_at=True,
        )
        save_pine_state(state)
        return {
            "ok": True,
            "duplicate": False,
            "state": state.model_dump(),
            "ticket": final_ticket.model_dump(),
            "match": match_payload,
            "live_mark": live_mark,
        }

    if not state.entry_ticket_id:
        raise RuntimeError(f"No active entry ticket found for state {state.state_id}.")
    entry_ticket = load_ticket(state.entry_ticket_id)
    if entry_ticket is None:
        raise RuntimeError(f"Entry ticket not found: {state.entry_ticket_id}")
    live_mark = _refresh_live_mark(
        entry_ticket,
        client=live_client,
        fallback_entry_credit=event.target_entry_credit,
    )

    if event.event_type == "MANAGE":
        if not state.active:
            state = _apply_state_event(
                state,
                event=event,
                status=state.status,
                active=False,
                latest_live_mark=live_mark,
                warnings=[
                    "Ignored MANAGE alert because the Pine bridge state is not active. "
                    f"Last event before this alert was {state.last_event_type or 'n/a'}.",
                ],
            )
            save_pine_state(state)
            return {
                "ok": True,
                "duplicate": False,
                "ignored": True,
                "state": state.model_dump(),
                "ticket": entry_ticket.model_dump(),
                "live_mark": live_mark,
            }
        state = _apply_state_event(
            state,
            event=event,
            status="open",
            active=True,
            latest_live_mark=live_mark,
        )
        save_pine_state(state)
        return {
            "ok": True,
            "duplicate": False,
            "state": state.model_dump(),
            "ticket": entry_ticket.model_dump(),
            "live_mark": live_mark,
        }

    close_ticket = _build_close_ticket(entry_ticket, event, live_mark=live_mark)
    close_limit = event.target_close_debit or _as_float(live_mark.get("close_mid_debit"))
    if close_limit is None:
        raise RuntimeError("Could not determine a live close debit for CLOSE alert.")
    first_leg = close_ticket.legs[0]
    prepared_close = prepare_ticket_for_preview(
        close_ticket,
        expiry_year=int(first_leg.expiry_year or 0),
        expiry_month=int(first_leg.expiry_month or 0),
        expiry_day=int(first_leg.expiry_day or 0),
        quantity=event.quantity or state.quantity,
        limit_price=float(close_limit),
        client_order_id=close_ticket.broker.client_order_id,
        price_type="NET_DEBIT",
        note="Prepared automatically from Pine bridge close alert.",
    )
    save_ticket(prepared_close)
    pending_close = mark_ticket_preview_pending(prepared_close)
    save_ticket(pending_close)
    try:
        close_preview = live_client.preview_order(
            pending_close.broker.account_id_key,
            pending_close.preview_request,
        )
        final_close_ticket = mark_ticket_previewed(pending_close, preview_response=close_preview)
    except Exception as exc:
        final_close_ticket = mark_ticket_preview_failed(pending_close, error_message=str(exc))
        save_ticket(final_close_ticket)
        state = _apply_state_event(
            state,
            event=event,
            status="error",
            active=False,
            close_ticket_id=final_close_ticket.ticket_id,
            latest_live_mark=live_mark,
            warnings=[str(exc)],
        )
        save_pine_state(state)
        return {
            "ok": False,
            "duplicate": False,
            "state": state.model_dump(),
            "ticket": final_close_ticket.model_dump(),
            "live_mark": live_mark,
        }
    save_ticket(final_close_ticket)
    state = _apply_state_event(
        state,
        event=event,
        status="closed",
        active=False,
        close_ticket_id=final_close_ticket.ticket_id,
        latest_live_mark=live_mark,
    )
    save_pine_state(state)
    return {
        "ok": True,
        "duplicate": False,
        "state": state.model_dump(),
        "entry_ticket": entry_ticket.model_dump(),
        "close_ticket": final_close_ticket.model_dump(),
        "live_mark": live_mark,
    }
