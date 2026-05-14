from __future__ import annotations

import logging
import os
import json
import secrets
from datetime import datetime, timezone
from html import escape
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starship_shared.schemas import EngineFactV1
from starship_shared.signing import verify_body_v1

from starship_web.config import get_logs_dir, load_runtime_env
from starship_web.slack import (
    format_fact_message,
    get_slack_webhook_url,
    send_slack_message,
)
from starship_web.etrade_auth import (
    build_authorize_url,
    clear_access_token,
    clear_pending_request_token,
    exchange_access_token,
    get_session_file_path,
    load_access_token,
    load_consumer_config,
    load_pending_request_token,
    renew_stored_access_token,
    request_token,
    save_access_token,
    save_pending_request_token,
    token_expiry_status,
)
from starship_web.etrade_client import load_api_config, load_client
from starship_web.execution_service import (
    build_place_order_request,
    get_execution_mode,
    submit_execution_package,
)
from starship_web.pine_bridge import get_tradingview_webhook_secret, process_pine_alert
from starship_web.pine_state_store import clear_pine_states, list_pine_states, load_pine_state
from starship_web.ticket_audit import (
    append_ticket_audit_event,
    clear_ticket_audit_logs,
    list_ticket_audit_events,
    load_latest_ticket_audit_event,
)
from starship_web.ticket_store import clear_tickets, list_tickets, load_ticket, save_ticket
from starship_shared.trade_ticket import TradeTicketV1
from starship_web.trade_tickets import (
    build_trade_ticket_from_preview,
    build_trade_ticket_from_signal,
    mark_ticket_preview_failed,
    mark_ticket_preview_pending,
    mark_ticket_previewed,
    prepare_ticket_for_preview,
    rebuild_ticket_preview_request,
)

_dotenv_path, _secrets_dir = load_runtime_env()

app = FastAPI()
basic_auth = HTTPBasic()


def _setup_logging(log_file: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger("starship_web")
    if logger.handlers:
        return logger
    log_path = Path(log_file) if log_file else (get_logs_dir() / "web.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5)
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def _load_engine_ingest_secret() -> str:
    env_secret = os.environ.get("ENGINE_INGEST_SECRET")
    if env_secret:
        return env_secret
    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    if not cfg_path.exists():
        return ""
    try:
        import yaml
    except Exception:
        return ""
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return ""
    comms = data.get("comms") if isinstance(data, dict) else None
    if not isinstance(comms, dict):
        return ""
    secret = comms.get("engine_ingest_secret")
    return str(secret).strip() if secret else ""


def _default_account_id_key() -> str:
    return os.environ.get("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "").strip()


def _remap_signal_ticket_account_if_needed(ticket: TradeTicketV1) -> tuple[TradeTicketV1, bool]:
    current_default = _default_account_id_key()
    if not current_default:
        return ticket, False
    if ticket.source != "signal":
        return ticket, False
    if ticket.broker.account_id_key == current_default:
        return ticket, False

    warning = (
        "Signal ticket account key was remapped to the current "
        "ETRADE_DEFAULT_ACCOUNT_ID_KEY before preview."
    )
    warnings = [w for w in ticket.warnings if w != warning]
    warnings.append(warning)
    updated = ticket.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "broker": ticket.broker.model_copy(
                update={"account_id_key": current_default}
            ),
            "warnings": warnings[-25:],
        }
    )
    return updated, True


def _admin_username() -> str:
    return os.environ.get("STARSHIP_ADMIN_USER", "").strip()


def _admin_password() -> str:
    return os.environ.get("STARSHIP_ADMIN_PASSWORD", "").strip()


def _admin_configured() -> bool:
    return bool(_admin_username() and _admin_password())


def _require_admin(
    credentials: HTTPBasicCredentials = Depends(basic_auth),
) -> str:
    expected_username = _admin_username()
    expected_password = _admin_password()
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=503,
            detail=(
                "Admin credentials are not configured. Set STARSHIP_ADMIN_USER "
                "and STARSHIP_ADMIN_PASSWORD before using /ops/status."
            ),
        )
    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _pretty_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _retry_cooldown_message(ticket: TradeTicketV1) -> str | None:
    next_retry = _parse_iso_dt(ticket.next_retry_at)
    if next_retry is None:
        return None
    now = datetime.now(timezone.utc)
    if next_retry <= now:
        return None
    remaining = int((next_retry - now).total_seconds())
    minutes, seconds = divmod(max(remaining, 0), 60)
    return f"Retry available in about {minutes}m {seconds}s."


def _normalize_etrade_error(error_message: str) -> str:
    lower = error_message.lower()
    if "oauth_problem=token_expired" in lower:
        clear_access_token()
        return (
            "E*TRADE access token expired. E*TRADE access tokens expire at the end "
            "of the current calendar day in US Eastern time, so this session needs "
            "a fresh reconnect from /broker/etrade."
        )
    return error_message


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _format_age_minutes(value: str | None) -> str:
    parsed = _parse_iso_dt(value)
    if parsed is None:
        return "n/a"
    now = datetime.now(timezone.utc)
    delta_minutes = max((now - parsed).total_seconds() / 60.0, 0.0)
    return f"{delta_minutes:.1f}m"


def _live_execute_thresholds() -> dict[str, int]:
    return {
        "signal_max_age_minutes": _env_int("STARSHIP_MAX_SIGNAL_AGE_MINUTES", 60),
        "preview_max_age_minutes": _env_int("STARSHIP_MAX_PREVIEW_AGE_MINUTES", 15),
        "snapshot_max_age_minutes": _env_int("STARSHIP_MAX_SNAPSHOT_AGE_MINUTES", 15),
    }


def _ticket_preview_succeeded(ticket: TradeTicketV1) -> bool:
    return bool(ticket.broker.preview_id) or ticket.status == "previewed"


def _ticket_snapshot_captured_at(ticket: TradeTicketV1) -> str | None:
    raw = ticket.market_snapshot.get("captured_at")
    return str(raw) if raw else None


def _is_within_minutes(value: str | None, max_age_minutes: int) -> bool:
    parsed = _parse_iso_dt(value)
    if parsed is None:
        return False
    now = datetime.now(timezone.utc)
    age_minutes = max((now - parsed).total_seconds() / 60.0, 0.0)
    return age_minutes <= float(max_age_minutes)


def _live_execute_readiness(ticket: TradeTicketV1) -> dict[str, object]:
    thresholds = _live_execute_thresholds()
    checks = {
        "execution_intent_prepared": ticket.execution_intent.ready_for_execution,
        "user_confirmed": ticket.execution_intent.user_confirmed,
        "matched_contracts": _ticket_has_matched_contracts(ticket),
        "preview_succeeded": _ticket_preview_succeeded(ticket),
        "signal_fresh": (
            True
            if ticket.source != "signal"
            else _is_within_minutes(
                ticket.created_at,
                thresholds["signal_max_age_minutes"],
            )
        ),
        "preview_fresh": _is_within_minutes(
            ticket.last_preview_at,
            thresholds["preview_max_age_minutes"],
        ),
        "snapshot_fresh": _is_within_minutes(
            _ticket_snapshot_captured_at(ticket),
            thresholds["snapshot_max_age_minutes"],
        ),
    }
    labels = {
        "execution_intent_prepared": "execution intent prepared",
        "user_confirmed": "user confirmed",
        "matched_contracts": "all legs matched to broker contracts",
        "preview_succeeded": "broker preview succeeded",
        "signal_fresh": "signal still fresh",
        "preview_fresh": "preview still fresh",
        "snapshot_fresh": "market snapshot still fresh",
    }
    blockers = [labels[key] for key, ok in checks.items() if not ok]
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "labels": labels,
        "blockers": blockers,
        "thresholds": thresholds,
        "ages": {
            "signal_age": _format_age_minutes(ticket.created_at),
            "preview_age": _format_age_minutes(ticket.last_preview_at),
            "snapshot_age": _format_age_minutes(_ticket_snapshot_captured_at(ticket)),
        },
    }


def _load_engine_comms_settings() -> dict[str, object]:
    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    payload: dict[str, object] = {
        "config_path": str(cfg_path),
        "engine_ingest_url": None,
        "engine_facts_jsonl": None,
    }
    if not cfg_path.exists():
        return payload
    try:
        import yaml
    except Exception:
        return payload
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return payload
    comms = data.get("comms") if isinstance(data, dict) else None
    if not isinstance(comms, dict):
        return payload
    payload["engine_ingest_url"] = comms.get("engine_ingest_url")
    payload["engine_facts_jsonl"] = comms.get("engine_facts_jsonl")
    return payload


def _resolve_local_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _path_mtime_iso(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _ticket_has_matched_contracts(ticket: TradeTicketV1) -> bool:
    return bool(ticket.legs) and all(bool(leg.osi_key) for leg in ticket.legs)


def _mock_execute_gate(ticket: TradeTicketV1) -> dict[str, bool]:
    return {
        "execution_intent_prepared": ticket.execution_intent.ready_for_execution,
        "user_confirmed": ticket.execution_intent.user_confirmed,
        "matched_contracts": _ticket_has_matched_contracts(ticket),
    }


def _ticket_can_mock_execute(ticket: TradeTicketV1) -> bool:
    return all(_mock_execute_gate(ticket).values())


def _audit_ticket(
    ticket: TradeTicketV1,
    *,
    category: str,
    action: str,
    summary: str,
    actor: str = "system",
    details: dict[str, object] | None = None,
) -> None:
    append_ticket_audit_event(
        ticket_id=ticket.ticket_id,
        category=category,
        action=action,
        summary=summary,
        actor=actor,
        details=details,
    )


def _build_ops_snapshot() -> dict[str, object]:
    tickets = list_tickets()
    pine_states = list_pine_states()
    latest_ticket = tickets[0] if tickets else None
    status_counts: dict[str, int] = {}
    live_execute_ready_count = 0
    for ticket in tickets:
        status_counts[ticket.status] = status_counts.get(ticket.status, 0) + 1
        if _live_execute_readiness(ticket)["ready"]:
            live_execute_ready_count += 1

    engine_comms = _load_engine_comms_settings()
    facts_path = _resolve_local_path(
        str(engine_comms.get("engine_facts_jsonl") or "")
    )

    try:
        api_config = load_api_config()
        broker_environment = api_config.environment
        broker_base_url = api_config.base_url
    except Exception as exc:
        broker_environment = f"unavailable: {exc}"
        broker_base_url = None
    latest_event = (
        load_latest_ticket_audit_event(latest_ticket.ticket_id)
        if latest_ticket is not None
        else None
    )

    return {
        "web": {
            "ok": True,
            "dotenv_path": str(_dotenv_path) if _dotenv_path else None,
            "secrets_dir": str(_secrets_dir) if _secrets_dir else None,
            "admin_auth_configured": _admin_configured(),
        },
        "broker": {
            "provider": "etrade",
            "connected": load_access_token() is not None,
            "pending_request_token": load_pending_request_token() is not None,
            "environment": broker_environment,
            "base_url": broker_base_url,
            "default_account_configured": bool(_default_account_id_key()),
            "execution_mode": get_execution_mode(),
        },
        "slack": {
            "configured": bool(get_slack_webhook_url()),
        },
        "engine": {
            "config_path": engine_comms.get("config_path"),
            "ingest_secret_configured": bool(ENGINE_INGEST_SECRET),
            "ingest_url": engine_comms.get("engine_ingest_url"),
            "facts_path": str(facts_path) if facts_path else None,
            "facts_exists": bool(facts_path and facts_path.exists()),
            "facts_last_modified": _path_mtime_iso(facts_path),
        },
        "tickets": {
            "count": len(tickets),
            "live_execute_ready_count": live_execute_ready_count,
            "status_counts": status_counts,
            "latest": (
                {
                    "ticket_id": latest_ticket.ticket_id,
                    "underlier": latest_ticket.underlier,
                    "status": latest_ticket.status,
                    "updated_at": latest_ticket.updated_at,
                    "mock_execute_ready": _ticket_can_mock_execute(latest_ticket),
                    "live_execute_ready": _live_execute_readiness(latest_ticket)["ready"],
                    "latest_activity": (
                        latest_event.model_dump() if latest_event is not None else None
                    ),
                }
                if latest_ticket is not None
                else None
            ),
        },
        "pine_bridge": {
            "configured": bool(get_tradingview_webhook_secret()),
            "state_count": len(pine_states),
            "latest": (
                pine_states[0].model_dump()
                if pine_states
                else None
            ),
        },
    }


def _clear_ticket_runtime_state(*, include_pine_states: bool = False) -> dict[str, int]:
    ticket_count = clear_tickets()
    audit_count = clear_ticket_audit_logs()
    pine_count = clear_pine_states() if include_pine_states else 0
    return {
        "tickets": ticket_count,
        "audit_logs": audit_count,
        "pine_states": pine_count,
    }


def _extract_accounts(payload: dict[str, object]) -> list[dict[str, object]]:
    accounts = payload.get("AccountListResponse", {}) if isinstance(payload, dict) else {}
    entries = accounts.get("Accounts", {}).get("Account", []) if isinstance(accounts, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    return [entry for entry in entries if isinstance(entry, dict)]


def _infer_leg_call_put(ticket: TradeTicketV1, leg_index: int) -> str:
    leg = ticket.legs[leg_index]
    if leg.call_put in ("PUT", "CALL"):
        return leg.call_put
    source_strike_key = str(leg.broker_payload.get("source_strike_key", ""))
    if "put" in source_strike_key or leg_index <= 1:
        return "PUT"
    return "CALL"


def _broker_symbol_candidates_for_ticket(ticket: TradeTicketV1) -> list[str]:
    candidates: list[str] = []
    for leg in ticket.legs:
        raw = leg.broker_payload.get("broker_symbol_candidates")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item not in candidates:
                    candidates.append(item)
    if not candidates:
        candidates.append(ticket.legs[0].symbol if ticket.legs else ticket.underlier)
    return candidates


def _extract_expiry_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    root = payload.get("OptionExpireDateResponse", {}) if isinstance(payload, dict) else {}
    rows = root.get("ExpirationDate", []) if isinstance(root, dict) else []
    if isinstance(rows, dict):
        rows = [rows]
    return [row for row in rows if isinstance(row, dict)]


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


def _derive_credit_suggestion(ticket: TradeTicketV1, matches: list[dict[str, object]]) -> dict[str, float | None]:
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

    suggested_mid = _round_price(max(net_mid, 0.01))
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
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "legs": legs,
    }


def _selected_expiry_from_chain(chain_payload: dict[str, object]) -> tuple[int, int, int] | None:
    root = chain_payload.get("OptionChainResponse", {}) if isinstance(chain_payload, dict) else {}
    selected = root.get("SelectedED", {}) if isinstance(root, dict) else {}
    if not isinstance(selected, dict):
        return None
    try:
        return int(selected["year"]), int(selected["month"]), int(selected["day"])
    except Exception:
        return None


def _apply_contract_matches(
    ticket: TradeTicketV1,
    *,
    matched_symbol: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    matches: list[dict[str, object]],
    suggestion_payload: dict[str, object] | None = None,
) -> TradeTicketV1:
    pricing_hints = _derive_credit_suggestion(ticket, matches)
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
    warnings = [
        warning
        for warning in ticket.warnings
        if "broker enrichment" not in warning.lower()
    ]
    return ticket.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "legs": legs,
            "pricing": ticket.pricing.model_copy(update=pricing_hints),
            "warnings": warnings,
            "broker_suggestions": suggestion_payload or ticket.broker_suggestions,
            "market_snapshot": market_snapshot,
        }
    )


def _suggest_contracts_for_ticket(ticket: TradeTicketV1) -> tuple[TradeTicketV1, dict[str, object]]:
    client = load_client()
    symbols = _broker_symbol_candidates_for_ticket(ticket)
    attempts: list[dict[str, object]] = []
    expiry_payloads: dict[str, object] = {}
    for symbol in symbols:
        try:
            expiries = client.get_option_expire_dates(symbol)
        except Exception as exc:
            attempts.append({"symbol": symbol, "error": str(exc)})
            continue
        expiry_payloads[symbol] = expiries
        expiry_rows = _extract_expiry_rows(expiries)
        for row in expiry_rows[:5]:
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
                )
            except Exception as exc:
                attempts.append({"symbol": symbol, "expiry": row, "error": str(exc)})
                continue
            matches = _find_matching_contracts(ticket, chain)
            attempts.append({"symbol": symbol, "expiry": row, "matched": matches is not None})
            if matches is not None:
                selected_expiry = _selected_expiry_from_chain(chain) or (year, month, day)
                enriched = _apply_contract_matches(
                    ticket,
                    matched_symbol=symbol,
                    expiry_year=selected_expiry[0],
                    expiry_month=selected_expiry[1],
                    expiry_day=selected_expiry[2],
                    matches=matches,
                    suggestion_payload={
                        "expiries": expiry_payloads,
                        "selected_symbol": symbol,
                        "selected_expiry": {
                            "year": selected_expiry[0],
                            "month": selected_expiry[1],
                            "day": selected_expiry[2],
                            "requested": row,
                        },
                        "matched_contracts": matches,
                        "attempts": attempts,
                    },
                )
                payload = {
                    "expiries": expiry_payloads,
                    "selected_symbol": symbol,
                    "selected_expiry": {
                        "year": selected_expiry[0],
                        "month": selected_expiry[1],
                        "day": selected_expiry[2],
                        "requested": row,
                    },
                    "matched_contracts": matches,
                    "attempts": attempts,
                }
                save_ticket(enriched)
                return enriched, payload
    payload = {"expiries": expiry_payloads, "attempts": attempts, "symbols": symbols}
    return ticket, payload


def _auto_enrich_signal_ticket(ticket: TradeTicketV1) -> TradeTicketV1:
    if ticket.source != "signal":
        return ticket
    try:
        enriched, payload = _suggest_contracts_for_ticket(ticket)
        matched = payload.get("matched_contracts")
        if matched:
            return enriched.model_copy(
                update={
                    "warnings": [
                        warning
                        for warning in enriched.warnings
                        if "broker enrichment" not in warning.lower()
                    ]
                }
            )
        attempts = payload.get("attempts")
        warning = (
            f"Auto-suggest could not find broker contract matches for {ticket.underlier}."
        )
        warnings = [w for w in ticket.warnings if w != warning]
        warnings.append(warning)
        if attempts:
            warnings.append(f"Broker enrichment attempts: {len(attempts)}")
        return ticket.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "warnings": warnings,
                "broker_suggestions": payload,
            }
        )
    except Exception as exc:
        warning = f"Auto-suggest broker enrichment failed: {exc}"
        warnings = [w for w in ticket.warnings if not w.startswith("Auto-suggest broker enrichment failed:")]
        warnings.append(warning)
        return ticket.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "warnings": warnings,
                "broker_suggestions": {"error": str(exc)},
            }
        )


def _render_etrade_console(
    *,
    message: str | None = None,
    error: str | None = None,
    accounts_payload: dict[str, object] | None = None,
    balance_payload: dict[str, object] | None = None,
    preview_payload: dict[str, object] | None = None,
    preview_request_text: str | None = None,
    selected_account_id_key: str = "",
) -> HTMLResponse:
    token = load_access_token()
    pending = load_pending_request_token()
    token_status = token_expiry_status(token)
    api_config = None
    try:
        api_config = load_api_config()
    except Exception:
        api_config = None

    accounts = _extract_accounts(accounts_payload or {})
    accounts_options = []
    for account in accounts:
        account_key = str(account.get("accountIdKey", ""))
        account_desc = str(account.get("accountDesc", ""))
        account_mode = str(account.get("accountMode", ""))
        selected = " selected" if account_key == selected_account_id_key else ""
        label = f"{account_key} | {account_desc or 'account'} | {account_mode or 'n/a'}"
        accounts_options.append(
            f'<option value="{escape(account_key)}"{selected}>{escape(label)}</option>'
        )
    accounts_options_html = "\n".join(accounts_options)

    sample_preview = preview_request_text or _pretty_json(
        {
            "PreviewOrderRequest": {
                "orderType": "OPTN",
                "clientOrderId": "starship-preview-001",
                "Order": [
                    {
                        "allOrNone": False,
                        "priceType": "NET_CREDIT",
                        "orderTerm": "GOOD_FOR_DAY",
                        "marketSession": "REGULAR",
                        "stopPrice": 0,
                        "limitPrice": 1.00,
                        "Instrument": [
                            {
                                "Product": {
                                    "securityType": "OPTN",
                                    "symbol": "SPXW"
                                },
                                "orderAction": "SELL_OPEN",
                                "quantityType": "QUANTITY",
                                "quantity": 1
                            }
                        ]
                    }
                ]
            }
        }
    )

    status_lines = [
        f"Token stored: {'yes' if token else 'no'}",
        f"Pending auth token: {'yes' if pending else 'no'}",
        "Auto-renew on inactive token: yes, then retry once",
    ]
    if token_status.get("has_token"):
        status_lines.append(
            f"Token issued at: {token_status.get('issued_at') or 'unknown'}"
        )
        status_lines.append(
            f"Token expires at (ET): {token_status.get('expires_at_eastern') or 'unknown'}"
        )
        status_lines.append(
            f"Token likely expired: {'yes' if token_status.get('expired') else 'no'}"
        )
    if api_config is not None:
        status_lines.append(f"Environment: {api_config.environment}")
        status_lines.append(f"Base URL: {api_config.base_url}")

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Starship E*TRADE Console</title>
  <style>
    :root {{
      --bg: #f5efe4;
      --panel: #fffaf1;
      --ink: #1f2933;
      --muted: #5b6572;
      --line: #d8c9aa;
      --accent: #0f766e;
      --accent-2: #8b5e34;
      --danger: #a11d33;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.12), transparent 35%),
        linear-gradient(180deg, #f7f3ea 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .lead {{
      color: var(--muted);
      max-width: 70ch;
      margin-bottom: 24px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 18px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    .message {{
      border-left: 4px solid var(--accent);
      padding: 12px 14px;
      margin-bottom: 18px;
      background: rgba(15, 118, 110, 0.08);
    }}
    .error {{
      border-left-color: var(--danger);
      background: rgba(161, 29, 51, 0.08);
    }}
    label {{
      display: block;
      font-size: 14px;
      margin-bottom: 8px;
      color: var(--muted);
    }}
    input, select, textarea, button {{
      width: 100%;
      box-sizing: border-box;
      border-radius: 10px;
      border: 1px solid var(--line);
      padding: 10px 12px;
      font: inherit;
      background: white;
      color: var(--ink);
    }}
    textarea {{
      min-height: 260px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
    }}
    button {{
      cursor: pointer;
      background: var(--accent);
      color: white;
      border: 0;
      font-weight: 600;
    }}
    button.alt {{
      background: var(--accent-2);
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      padding: 14px;
      border-radius: 12px;
      background: #f8f4ec;
      border: 1px solid var(--line);
      overflow-x: auto;
    }}
    ul {{
      padding-left: 18px;
      color: var(--muted);
    }}
    .actions {{
      display: grid;
      gap: 10px;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("etrade")}
    <h1>Starship E*TRADE Console</h1>
    <p class="lead">This console is the safe bridge between your signal engine and future execution UI. For now it only handles connection checks, account reads, and preview-only order requests.</p>
    {f'<div class="message">{escape(message)}</div>' if message else ''}
    {f'<div class="message error">{escape(error)}</div>' if error else ''}
    <div class="grid">
      <section class="panel">
        <h2>Connection</h2>
        <ul>
          {''.join(f'<li>{escape(line)}</li>' for line in status_lines)}
        </ul>
        <div class="actions">
          <form method="get" action="/broker/etrade/connect">
            <button type="submit">Get Authorization Link</button>
          </form>
          <form method="post" action="/broker/etrade/connect/start">
            <button type="submit" class="alt">Open Authorization Flow</button>
          </form>
          <form method="post" action="/broker/etrade/renew/browser">
            <button type="submit">Renew Stored Token</button>
          </form>
        </div>
        <form method="post" action="/broker/etrade/complete" style="margin-top:16px;">
          <label for="oauth_verifier">OAuth verifier from E*TRADE</label>
          <input id="oauth_verifier" name="oauth_verifier" placeholder="Paste verifier here" />
          <button type="submit" style="margin-top:10px;">Store Access Token</button>
        </form>
      </section>
      <section class="panel">
        <h2>Accounts</h2>
        <div class="actions">
          <form method="get" action="/broker/etrade/accounts">
            <button type="submit">Fetch Accounts (JSON)</button>
          </form>
          <form method="post" action="/broker/etrade/console/accounts">
            <button type="submit">Load Accounts In Console</button>
          </form>
        </div>
        <form method="post" action="/broker/etrade/console/balance" style="margin-top:16px;">
          <label for="account_id_key_balance">Account for balance lookup</label>
          <select id="account_id_key_balance" name="account_id_key">
            <option value="">Select an account</option>
            {accounts_options_html}
          </select>
          <button type="submit" style="margin-top:10px;">Load Balance</button>
        </form>
      </section>
      <section class="panel" style="grid-column: 1 / -1;">
        <h2>Preview Order</h2>
        <form method="post" action="/broker/etrade/console/preview">
          <label for="account_id_key_preview">Account for preview</label>
          <select id="account_id_key_preview" name="account_id_key">
            <option value="">Select an account</option>
            {accounts_options_html}
          </select>
          <label for="preview_payload_json" style="margin-top:12px;">Preview request JSON</label>
          <textarea id="preview_payload_json" name="preview_payload_json">{escape(sample_preview)}</textarea>
          <button type="submit" style="margin-top:10px;">Submit Preview Only</button>
        </form>
      </section>
    </div>
    {f'<section class="panel" style="margin-top:18px;"><h2>Accounts Response</h2><pre>{escape(_pretty_json(accounts_payload))}</pre></section>' if accounts_payload else ''}
    {f'<section class="panel" style="margin-top:18px;"><h2>Balance Response</h2><pre>{escape(_pretty_json(balance_payload))}</pre></section>' if balance_payload else ''}
    {f'<section class="panel" style="margin-top:18px;"><h2>Preview Response</h2><pre>{escape(_pretty_json(preview_payload))}</pre></section>' if preview_payload else ''}
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _app_nav_styles() -> str:
    return """
    .app-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 22px;
      padding: 14px 16px;
      background: rgba(255, 250, 241, 0.88);
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      box-shadow: 0 10px 24px rgba(63, 46, 25, 0.06);
      backdrop-filter: blur(10px);
    }
    .app-nav a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      padding: 0 14px;
      border-radius: 999px;
      text-decoration: none;
      color: #8b5e34;
      background: rgba(139, 94, 52, 0.08);
      border: 1px solid transparent;
      font-weight: 600;
    }
    .app-nav a:hover {
      border-color: #d8c9aa;
      background: rgba(15, 118, 110, 0.10);
      color: #0f766e;
    }
    .app-nav a.active {
      background: #0f766e;
      color: white;
      border-color: #0f766e;
    }
    """


def _render_app_nav(active: str = "") -> str:
    links = [
        ("/", "Home", "home"),
        ("/broker/etrade", "E*TRADE", "etrade"),
        ("/tickets", "Tickets", "tickets"),
        ("/pine-bridge", "Pine Bridge", "pine"),
        ("/ops/status", "Ops", "ops"),
    ]
    items = []
    for href, label, key in links:
        active_class = "active" if active == key else ""
        items.append(
            f'<a class="{active_class}" href="{escape(href)}">{escape(label)}</a>'
        )
    return f'<nav class="app-nav">{"".join(items)}</nav>'


def _render_home(snapshot: dict[str, object]) -> HTMLResponse:
    broker_info = snapshot.get("broker", {}) if isinstance(snapshot, dict) else {}
    tickets_info = snapshot.get("tickets", {}) if isinstance(snapshot, dict) else {}
    pine_info = snapshot.get("pine_bridge", {}) if isinstance(snapshot, dict) else {}
    latest_ticket = (
        tickets_info.get("latest", {}) if isinstance(tickets_info, dict) else {}
    )
    latest_pine = pine_info.get("latest", {}) if isinstance(pine_info, dict) else {}
    latest_pine_close_text = "n/a"
    if isinstance(latest_pine, dict):
        pine_events = latest_pine.get("event_log")
        if isinstance(pine_events, list):
            close_events = [
                event
                for event in pine_events
                if isinstance(event, dict)
                and str(event.get("event_type") or "").upper() == "CLOSE"
            ]
            if close_events:
                latest_close = close_events[-1]
                latest_pine_close_text = (
                    f"{_bridge_event_reason(latest_close)} at "
                    f"{latest_close.get('processed_at') or latest_close.get('bar_time') or 'n/a'}"
                )

    cards = [
        {
            "title": "E*TRADE Console",
            "href": "/broker/etrade",
            "body": (
                "Check connection health, pull accounts, and submit preview-only "
                "broker requests."
            ),
            "meta": (
                f"Connected: {broker_info.get('connected')} | "
                f"Mode: {broker_info.get('execution_mode') or 'n/a'}"
            ),
        },
        {
            "title": "Ticket Queue",
            "href": "/tickets",
            "body": (
                "Review all preview tickets, execution readiness, and per-trade "
                "details in one place."
            ),
            "meta": (
                f"Tickets: {tickets_info.get('count', 0)} | "
                f"Live-ready: {tickets_info.get('live_execute_ready_count', 0)}"
            ),
        },
        {
            "title": "Pine Bridge",
            "href": "/pine-bridge",
            "body": (
                "Inspect TradingView/Pine webhook state, linked tickets, and the "
                "latest live mark snapshots."
            ),
            "meta": (
                f"Configured: {pine_info.get('configured')} | "
                f"States: {pine_info.get('state_count', 0)}"
            ),
        },
        {
            "title": "Ops Status",
            "href": "/ops/status",
            "body": (
                "Open the authenticated deployment snapshot for web, broker, Slack, "
                "engine handoff, and ticket state."
            ),
            "meta": "Protected with basic auth.",
        },
    ]

    card_html = "".join(
        f"""
        <a class="card" href="{escape(str(card['href']))}">
          <h2>{escape(str(card['title']))}</h2>
          <p>{escape(str(card['body']))}</p>
          <span>{escape(str(card['meta']))}</span>
        </a>
        """
        for card in cards
    )

    latest_ticket_html = (
        f"""
        <section class="panel">
          <h2>Latest Ticket</h2>
          <p><strong>ID:</strong> <a href="/tickets/{escape(str(latest_ticket.get('ticket_id') or ''))}">{escape(str(latest_ticket.get('ticket_id') or 'n/a'))}</a></p>
          <p><strong>Underlier:</strong> {escape(str(latest_ticket.get('underlier') or 'n/a'))}</p>
          <p><strong>Status:</strong> {escape(str(latest_ticket.get('status') or 'n/a'))}</p>
          <p><strong>Updated:</strong> {escape(str(latest_ticket.get('updated_at') or 'n/a'))}</p>
        </section>
        """
        if latest_ticket
        else """
        <section class="panel">
          <h2>Latest Ticket</h2>
          <p>No tickets yet.</p>
        </section>
        """
    )

    latest_pine_html = (
        f"""
        <section class="panel">
          <h2>Latest Pine State</h2>
          <p><strong>State:</strong> <a href="/pine-bridge/states/{escape(str(latest_pine.get('state_id') or ''))}">{escape(str(latest_pine.get('state_id') or 'n/a'))}</a></p>
          <p><strong>Strategy:</strong> {escape(str(latest_pine.get('strategy_name') or 'n/a'))}</p>
          <p><strong>Status:</strong> {escape(str(latest_pine.get('status') or 'n/a'))}</p>
          <p><strong>Last Event:</strong> {escape(str(latest_pine.get('last_event_type') or 'n/a'))}</p>
          <p><strong>Last Close:</strong> {escape(latest_pine_close_text)}</p>
        </section>
        """
        if latest_pine
        else """
        <section class="panel">
          <h2>Latest Pine State</h2>
          <p>No Pine bridge states yet.</p>
        </section>
        """
    )

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Starship Home</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.12), transparent 35%),
        linear-gradient(180deg, #f7f3ea 0%, #efe6d5 100%);
      color: #1f2933;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1, h2 {{
      margin-top: 0;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .lead {{
      max-width: 72ch;
      margin-bottom: 24px;
      color: #5b6572;
    }}
    .card-grid, .panel-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 16px;
    }}
    .card, .panel {{
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    .card {{
      text-decoration: none;
      color: inherit;
      transition: transform 120ms ease, box-shadow 120ms ease;
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 16px 34px rgba(63, 46, 25, 0.12);
    }}
    .card p {{
      color: #5b6572;
      line-height: 1.5;
    }}
    .card span {{
      display: block;
      margin-top: 12px;
      color: #8b5e34;
      font-weight: 600;
    }}
    a {{
      color: #8b5e34;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("home")}
    <h1>Starship Console</h1>
    <p class="lead">This landing page gives you one place to jump between broker connection, preview tickets, Pine bridge state, and ops status while you are testing the TradingView to E*TRADE flow.</p>
    <section class="card-grid">
      {card_html}
    </section>
    <section class="panel-grid" style="margin-top:18px;">
      {latest_ticket_html}
      {latest_pine_html}
    </section>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _bridge_event_reason(event: dict[str, object]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    parts = event_id.split("|")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return event_id or "n/a"


def _latest_bridge_event(state: object, event_type: str) -> dict[str, object] | None:
    target = event_type.upper()
    events = [
        event
        for event in getattr(state, "event_log", [])
        if str(event.get("event_type") or "").upper() == target
    ]
    return events[-1] if events else None


def _render_bridge_event_timeline(state: object, *, limit: int = 10) -> str:
    events = list(getattr(state, "event_log", []) or [])[-limit:]
    if not events:
        return "<p>No Pine events recorded yet.</p>"

    rows = []
    for event in reversed(events):
        event_type = str(event.get("event_type") or "n/a").upper()
        css_event = event_type.lower()
        reason = _bridge_event_reason(event)
        processed = str(event.get("processed_at") or "n/a")
        bar_time = str(event.get("bar_time") or "n/a")
        event_id = str(event.get("event_id") or "n/a")
        rows.append(
            f"""
            <tr class="event-{escape(css_event)}">
              <td class="event-type">{escape(event_type)}</td>
              <td>{escape(reason)}</td>
              <td>{escape(processed)}</td>
              <td>{escape(bar_time)}</td>
              <td><code>{escape(event_id)}</code></td>
            </tr>
            """
        )

    return f"""
    <table class="event-table">
      <thead>
        <tr>
          <th>Type</th>
          <th>Reason</th>
          <th>Processed</th>
          <th>Bar Time</th>
          <th>Event ID</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


def _render_pine_bridge_overview() -> HTMLResponse:
    states = list_pine_states()
    state_cards = []
    for state in states:
        entry_ticket = load_ticket(state.entry_ticket_id) if state.entry_ticket_id else None
        close_ticket = load_ticket(state.close_ticket_id) if state.close_ticket_id else None
        live_mark = state.latest_live_mark or {}
        live_pnl = live_mark.get("estimated_pnl")
        pnl_text = "n/a" if live_pnl is None else str(live_pnl)
        latest_close = _latest_bridge_event(state, "CLOSE")
        latest_close_text = "n/a"
        if latest_close:
            latest_close_text = (
                f"{_bridge_event_reason(latest_close)} at "
                f"{latest_close.get('processed_at') or latest_close.get('bar_time') or 'n/a'}"
            )
        state_cards.append(
            f"""
            <article class="panel">
              <h2>{escape(state.underlier)} <span>{escape(state.status)}</span></h2>
              <p><strong>State ID:</strong> <a href="/pine-bridge/states/{escape(state.state_id)}">{escape(state.state_id)}</a></p>
              <p><strong>Strategy:</strong> {escape(state.strategy_name)}</p>
              <p><strong>Position Key:</strong> {escape(state.position_key)}</p>
              <p><strong>Active:</strong> {escape(str(state.active))}</p>
              <p><strong>Last Event:</strong> {escape(str(state.last_event_type or 'n/a'))}</p>
              <p><strong>Last Close:</strong> {escape(latest_close_text)}</p>
              <p><strong>Updated:</strong> {escape(state.updated_at)}</p>
              <p><strong>Live P/L:</strong> {escape(pnl_text)}</p>
              <p><strong>Entry Ticket:</strong> {f'<a href="/tickets/{escape(state.entry_ticket_id)}">{escape(state.entry_ticket_id)}</a>' if entry_ticket else escape(str(state.entry_ticket_id or 'n/a'))}</p>
              <p><strong>Close Ticket:</strong> {f'<a href="/tickets/{escape(state.close_ticket_id)}">{escape(state.close_ticket_id)}</a>' if close_ticket else escape(str(state.close_ticket_id or 'n/a'))}</p>
              <details open>
                <summary>Recent Pine Events</summary>
                {_render_bridge_event_timeline(state)}
              </details>
              <details>
                <summary>Latest Aux</summary>
                <pre>{escape(_pretty_json(state.latest_aux))}</pre>
              </details>
              <details>
                <summary>Latest Live Mark</summary>
                <pre>{escape(_pretty_json(state.latest_live_mark))}</pre>
              </details>
            </article>
            """
        )

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Pine Bridge Overview</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: linear-gradient(180deg, #f7f3ea 0%, #efe6d5 100%);
      color: #1f2933;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    .panel {{
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    h1, h2 {{
      font-family: Georgia, "Times New Roman", serif;
      margin-top: 0;
    }}
    h2 span {{
      font-size: 14px;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: #8b5e34;
      margin-left: 8px;
      text-transform: uppercase;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8f4ec;
      border: 1px solid #d8c9aa;
      border-radius: 12px;
      padding: 12px;
    }}
    code {{
      color: #4b5563;
      font-size: 12px;
    }}
    .event-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 13px;
    }}
    .event-table th,
    .event-table td {{
      border-top: 1px solid #eadfca;
      padding: 7px 6px;
      text-align: left;
      vertical-align: top;
    }}
    .event-table th {{
      color: #6f5634;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .event-type {{
      font-weight: 700;
    }}
    .event-entry .event-type {{
      color: #0f766e;
    }}
    .event-manage .event-type {{
      color: #8b5e34;
    }}
    .event-close .event-type {{
      color: #b91c1c;
    }}
    a {{
      color: #8b5e34;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("pine")}
    <h1>Pine Bridge</h1>
    <p>This view tracks TradingView/Pine state objects, linked entry and close tickets, and the latest live mark data gathered on the Python side.</p>
    <div class="grid">
      {''.join(state_cards) or '<section class="panel"><h2>No Pine states yet</h2><p>Once TradingView sends ENTRY or MANAGE alerts, they will show up here.</p></section>'}
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _render_ops_status(
    snapshot: dict[str, object],
    *,
    viewer: str,
) -> HTMLResponse:
    web_info = snapshot.get("web", {}) if isinstance(snapshot, dict) else {}
    broker_info = snapshot.get("broker", {}) if isinstance(snapshot, dict) else {}
    slack_info = snapshot.get("slack", {}) if isinstance(snapshot, dict) else {}
    engine_info = snapshot.get("engine", {}) if isinstance(snapshot, dict) else {}
    tickets_info = snapshot.get("tickets", {}) if isinstance(snapshot, dict) else {}
    latest_ticket = (
        tickets_info.get("latest", {}) if isinstance(tickets_info, dict) else {}
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Starship Ops Status</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 30%),
        linear-gradient(180deg, #f7f3ea 0%, #efe6d5 100%);
      color: #1f2933;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    .panel {{
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    h1, h2 {{
      font-family: Georgia, "Times New Roman", serif;
      margin-top: 0;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8f4ec;
      border: 1px solid #d8c9aa;
      border-radius: 12px;
      padding: 12px;
    }}
    a {{
      color: #8b5e34;
    }}
    .status-ok {{
      color: #0f766e;
      font-weight: 600;
    }}
    .status-warn {{
      color: #8b5e34;
      font-weight: 600;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("ops")}
    <h1>Ops Status</h1>
    <p>Authenticated as <strong>{escape(viewer)}</strong>. This page is the safe deployment-health view for web, broker, Slack, and engine handoff.</p>
    <p><a href="/ops/status.json">Open JSON Snapshot</a></p>
    <div class="grid">
      <section class="panel">
        <h2>Web Runtime</h2>
        <p><strong>Healthy:</strong> <span class="status-ok">{escape(str(web_info.get('ok')))}</span></p>
        <p><strong>Admin Auth:</strong> {escape('configured' if web_info.get('admin_auth_configured') else 'missing')}</p>
        <p><strong>Env File:</strong> {escape(str(web_info.get('dotenv_path') or 'not found'))}</p>
        <p><strong>Secrets Dir:</strong> {escape(str(web_info.get('secrets_dir') or 'not set'))}</p>
      </section>
      <section class="panel">
        <h2>Broker + Slack</h2>
        <p><strong>E*TRADE Connected:</strong> {escape(str(broker_info.get('connected')))}</p>
        <p><strong>Pending Auth Token:</strong> {escape(str(broker_info.get('pending_request_token')))}</p>
        <p><strong>Broker Env:</strong> {escape(str(broker_info.get('environment') or 'n/a'))}</p>
        <p><strong>Default Account:</strong> {escape('configured' if broker_info.get('default_account_configured') else 'missing')}</p>
        <p><strong>Slack Webhook:</strong> {escape('configured' if slack_info.get('configured') else 'missing')}</p>
      </section>
      <section class="panel">
        <h2>Engine Handoff</h2>
        <p><strong>Config Path:</strong> {escape(str(engine_info.get('config_path') or 'n/a'))}</p>
        <p><strong>Ingest Secret:</strong> {escape('configured' if engine_info.get('ingest_secret_configured') else 'missing')}</p>
        <p><strong>Ingest URL:</strong> {escape(str(engine_info.get('ingest_url') or 'n/a'))}</p>
        <p><strong>Facts File:</strong> {escape(str(engine_info.get('facts_path') or 'n/a'))}</p>
        <p><strong>Facts Present:</strong> {escape(str(engine_info.get('facts_exists')))}</p>
        <p><strong>Facts Updated:</strong> {escape(str(engine_info.get('facts_last_modified') or 'n/a'))}</p>
      </section>
      <section class="panel">
        <h2>Ticket Queue</h2>
        <p><strong>Total Tickets:</strong> {escape(str(tickets_info.get('count', 0)))}</p>
        <p><strong>Live Execute Ready:</strong> {escape(str(tickets_info.get('live_execute_ready_count', 0)))}</p>
        <p><strong>Status Counts:</strong></p>
        <pre>{escape(_pretty_json(tickets_info.get('status_counts', {})))}</pre>
        <p><strong>Latest Ticket:</strong></p>
        <pre>{escape(_pretty_json(latest_ticket or {}))}</pre>
      </section>
      <section class="panel" style="grid-column: 1 / -1;">
        <h2>Raw Snapshot</h2>
        <pre>{escape(_pretty_json(snapshot))}</pre>
      </section>
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _render_ticket_queue(
    *,
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    tickets = list_tickets()
    cards = []
    for ticket in tickets:
        cooldown_message = _retry_cooldown_message(ticket)
        live_readiness = _live_execute_readiness(ticket)
        enrichment_badge = ""
        if any(leg.osi_key for leg in ticket.legs):
            enrichment_badge = '<p><strong>Broker Match:</strong> yes</p>'
        elif ticket.source == "signal":
            enrichment_badge = '<p><strong>Broker Match:</strong> pending/manual</p>'
        intent_badge = ""
        if ticket.execution_intent.ready_for_execution:
            intent_badge = '<p><strong>Execution Intent:</strong> prepared</p>'
        mock_execute_badge = ""
        if _ticket_can_mock_execute(ticket):
            mock_execute_badge = '<p><strong>Mock Execute:</strong> ready</p>'
        elif (
            ticket.execution_intent.ready_for_execution
            or ticket.execution_intent.user_confirmed
            or _ticket_has_matched_contracts(ticket)
        ):
            mock_execute_badge = '<p><strong>Mock Execute:</strong> gated</p>'
        live_execute_badge = (
            '<p><strong>Live Execute:</strong> ready</p>'
            if live_readiness["ready"]
            else f'<p><strong>Live Execute:</strong> blocked ({len(live_readiness["blockers"])} blockers)</p>'
        )
        prep_form = ""
        if ticket.source == "signal" and not ticket.preview_request:
            prep_form = f"""
              <details>
                <summary>Prepare Broker Preview</summary>
                <form method="post" action="/tickets/{escape(ticket.ticket_id)}/prepare-preview" class="prep-form">
                  <label>Expiry Year<input type="number" name="expiry_year" value="2026" required></label>
                  <label>Expiry Month<input type="number" name="expiry_month" min="1" max="12" value="4" required></label>
                  <label>Expiry Day<input type="number" name="expiry_day" min="1" max="31" value="19" required></label>
                  <label>Contracts<input type="number" name="quantity" min="1" value="1" required></label>
                  <label>Target Net Credit<input type="number" name="limit_price" min="0.01" step="0.01" value="1.00" required></label>
                  <button type="submit">Prepare Preview Request</button>
                </form>
              </details>
            """
        preview_summary = ""
        if ticket.preview_request:
            preview_summary = f"<p><strong>Preview request:</strong> ready</p>"
        warnings = "".join(
            f"<li>{escape(w)}</li>" for w in ticket.warnings[-3:]
        ) or "<li>None</li>"
        cards.append(
            f"""
            <article class="ticket">
              <h2>{escape(ticket.underlier)} <span>{escape(ticket.status)}</span></h2>
              <p><strong>Ticket:</strong> {escape(ticket.ticket_id)}<br>
              <strong>Strategy:</strong> {escape(ticket.strategy or 'n/a')}<br>
              <strong>Source:</strong> {escape(ticket.source)}<br>
              <strong>Account:</strong> {escape(ticket.broker.account_id_key or 'unset')}</p>
              <p><strong>Price:</strong> {escape(ticket.pricing.price_type)} {escape(str(ticket.pricing.limit_price)) if ticket.pricing.limit_price is not None else ''}</p>
              <p><strong>Preview attempts:</strong> {ticket.preview_attempts}</p>
              <p><strong>Last error:</strong> {escape(ticket.last_error or 'none')}</p>
              <p><strong>Status reason:</strong> {escape(ticket.status_reason or 'n/a')}</p>
              <p><strong>Retry timing:</strong> {escape(cooldown_message or 'ready now')}</p>
              {enrichment_badge}
              {intent_badge}
              {mock_execute_badge}
              {live_execute_badge}
              {preview_summary}
              <details>
                <summary>Legs</summary>
                <pre>{escape(_pretty_json([leg.model_dump() for leg in ticket.legs]))}</pre>
              </details>
              <details>
                <summary>Warnings</summary>
                <ul>{warnings}</ul>
              </details>
              {prep_form}
              <div class="actions">
                {'<form method="post" action="/tickets/' + escape(ticket.ticket_id) + '/retry-preview"><button type="submit">Retry Preview</button></form>' if ticket.preview_request else '<span>Prepare preview request first</span>'}
                <a href="/tickets/{escape(ticket.ticket_id)}">Open Ticket</a>
              </div>
            </article>
            """
        )

    maintenance_html = """
    <section class="maintenance">
      <div class="maintenance-copy">
        <h2>Queue Maintenance</h2>
        <p>Use these when stale ticket data is getting in the way. The first action clears the ticket queue and audit logs. The second also clears Pine bridge states for a full fresh start.</p>
      </div>
      <div class="maintenance-actions">
        <form method="post" action="/tickets/clear-all">
          <button type="submit" class="alt">Clear Tickets + Audit Logs</button>
        </form>
        <form method="post" action="/tickets/reset-day">
          <button type="submit" class="danger">Clear Tickets + Audit + Pine States</button>
        </form>
      </div>
    </section>
    """

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Starship Ticket Queue</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: linear-gradient(180deg, #f7f3ea 0%, #efe6d5 100%);
      color: #1f2933;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .message {{
      border-left: 4px solid #0f766e;
      padding: 12px 14px;
      margin-bottom: 18px;
      background: rgba(15, 118, 110, 0.08);
    }}
    .error {{
      border-left-color: #a11d33;
      background: rgba(161, 29, 51, 0.08);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    .ticket {{
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    h1, h2 {{
      font-family: Georgia, "Times New Roman", serif;
    }}
    h2 span {{
      font-size: 14px;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: #8b5e34;
      margin-left: 8px;
      text-transform: uppercase;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 12px;
    }}
    .prep-form {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
    input {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #d8c9aa;
      border-radius: 10px;
      padding: 8px 10px;
      margin-top: 4px;
    }}
    button {{
      border: 0;
      background: #0f766e;
      color: white;
      padding: 10px 12px;
      border-radius: 10px;
      cursor: pointer;
    }}
    button.alt {{
      background: #8b5e34;
    }}
    button.danger {{
      background: #a11d33;
    }}
    a {{
      color: #8b5e34;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8f4ec;
      border: 1px solid #d8c9aa;
      border-radius: 12px;
      padding: 12px;
    }}
    .maintenance {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
      justify-content: space-between;
      margin: 0 0 18px;
      padding: 18px;
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    .maintenance h2 {{
      margin: 0 0 8px;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .maintenance p {{
      margin: 0;
      max-width: 70ch;
      color: #5b6572;
    }}
    .maintenance-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("tickets")}
    <h1>Trade Ticket Queue</h1>
    <p>This queue is the canonical review surface for manual and signal-driven trade tickets.</p>
    {f'<div class="message">{escape(message)}</div>' if message else ''}
    {f'<div class="message error">{escape(error)}</div>' if error else ''}
    {maintenance_html}
    <div class="grid">
      {''.join(cards) or '<p>No tickets yet.</p>'}
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _render_ticket_detail(
    ticket: TradeTicketV1,
    *,
    message: str | None = None,
    error: str | None = None,
    expiry_suggestions: dict[str, object] | None = None,
) -> HTMLResponse:
    default_expiry_year = ticket.legs[0].expiry_year or 2026
    default_expiry_month = ticket.legs[0].expiry_month or 4
    default_expiry_day = ticket.legs[0].expiry_day or 19
    default_limit_price = (
        ticket.pricing.limit_price
        or ticket.pricing.suggested_limit_price
        or 1.00
    )
    default_quantity = int(ticket.legs[0].quantity or 1)
    pricing_note = ""
    if ticket.pricing.suggested_limit_price is not None:
        pricing_note = (
            f"<p><strong>Suggested Credit:</strong> {ticket.pricing.suggested_limit_price:.2f}"
            f" | <strong>Net Mid:</strong> {ticket.pricing.suggested_net_mid if ticket.pricing.suggested_net_mid is not None else 'n/a'}"
            f" | <strong>Natural:</strong> {ticket.pricing.suggested_natural_credit if ticket.pricing.suggested_natural_credit is not None else 'n/a'}</p>"
        )
    prep_form = ""
    if ticket.source == "signal" and not ticket.preview_request:
        prep_form = f"""
        <section class="panel">
          <h2>Prepare Broker Preview</h2>
          {pricing_note}
          <form method="post" action="/tickets/{escape(ticket.ticket_id)}/prepare-preview" class="prep-form">
            <label>Expiry Year<input type="number" name="expiry_year" value="{default_expiry_year}" required></label>
            <label>Expiry Month<input type="number" name="expiry_month" min="1" max="12" value="{default_expiry_month}" required></label>
            <label>Expiry Day<input type="number" name="expiry_day" min="1" max="31" value="{default_expiry_day}" required></label>
            <label>Contracts<input type="number" name="quantity" min="1" value="{default_quantity}" required></label>
            <label>Target Net Credit<input type="number" name="limit_price" min="0.01" step="0.01" value="{default_limit_price:.2f}" required></label>
            <button type="submit">Prepare Preview Request</button>
          </form>
          <form method="post" action="/tickets/{escape(ticket.ticket_id)}/suggest-expiries" style="margin-top:12px;">
            <button type="submit">Load Expiry Suggestions</button>
          </form>
          <form method="post" action="/tickets/{escape(ticket.ticket_id)}/auto-suggest-contracts" style="margin-top:12px;">
            <button type="submit">Auto-Suggest Matching Contracts</button>
          </form>
        </section>
        """

    retry_form = (
        f"""
        <form method="post" action="/tickets/{escape(ticket.ticket_id)}/retry-preview">
          <button type="submit">Retry Preview</button>
        </form>
        """
        if ticket.preview_request
        else "<p>Prepare preview request first.</p>"
    )
    confirm_form = ""
    if ticket.execution_intent.ready_for_execution:
        if ticket.execution_intent.user_confirmed:
            confirm_form = f"""
            <form method="post" action="/tickets/{escape(ticket.ticket_id)}/unconfirm-intent">
              <button type="submit">Remove Confirmation</button>
            </form>
            """
        else:
            confirm_form = f"""
            <form method="post" action="/tickets/{escape(ticket.ticket_id)}/confirm-intent">
              <button type="submit">Confirm Execution Intent</button>
            </form>
            """

    suggestions_html = ""
    if expiry_suggestions:
        suggestions_html = f"""
        <section class="panel">
          <h2>Expiry Suggestions</h2>
          <pre>{escape(_pretty_json(expiry_suggestions))}</pre>
        </section>
        """
    live_readiness = _live_execute_readiness(ticket)
    current_execution_mode = get_execution_mode()
    execution_service_payload = ticket.execution_record or {}
    execution_service_message = str(
        execution_service_payload.get("message")
        or "No execution package has been submitted through the execution service yet."
    )
    execution_service_button_label = {
        "disabled": "Execution Service Disabled",
        "dry_run": "Submit Execution Package (Dry Run)",
        "live": "Submit Execution Package (Live Guarded)",
    }.get(current_execution_mode, "Submit Execution Package")
    place_request_preview = ticket.place_order_request
    if not place_request_preview:
        try:
            if ticket.preview_request and ticket.broker.preview_id:
                place_request_preview = build_place_order_request(ticket)
        except Exception:
            place_request_preview = {}
    live_execute_checks = "".join(
        (
            f"<li><strong>{escape(live_readiness['labels'][key].title())}:</strong> "
            f"{'yes' if ready else 'no'}</li>"
        )
        for key, ready in live_readiness["checks"].items()
    )
    live_execute_blockers = "".join(
        f"<li>{escape(blocker)}</li>" for blocker in live_readiness["blockers"]
    ) or "<li>No blockers.</li>"
    audit_events = list_ticket_audit_events(ticket.ticket_id, limit=25)
    audit_items = "".join(
        (
            "<li>"
            f"<strong>{escape(event.created_at)}</strong> "
            f"{escape(event.summary)}"
            f"<br><span>{escape(event.category + ' / ' + event.action + ' / ' + event.actor)}</span>"
            "</li>"
        )
        for event in reversed(audit_events)
    ) or "<li>No activity recorded yet for this ticket.</li>"
    cooldown_message = _retry_cooldown_message(ticket)
    sandbox_banner = ""
    if ticket.status == "preview_failed" and ticket.status_reason and "sandbox preview service" in ticket.status_reason.lower():
        banner_text = ticket.status_reason
        if cooldown_message:
            banner_text += f" {cooldown_message}"
        sandbox_banner = f'<div class="message">{escape(banner_text)}</div>'

    execution_summary = {
        "ticket_id": ticket.ticket_id,
        "status": ticket.status,
        "underlier": ticket.underlier,
        "strategy": ticket.strategy,
        "broker": {
            "provider": ticket.broker.provider,
            "account_id_key": ticket.broker.account_id_key,
            "order_type": ticket.broker.order_type,
            "client_order_id": ticket.broker.client_order_id,
            "preview_id": ticket.broker.preview_id,
        },
        "execution_intent": ticket.execution_intent.model_dump(),
        "matched_contracts": [
            {
                "leg_id": leg.leg_id,
                "symbol": leg.symbol,
                "order_action": leg.order_action,
                "call_put": leg.call_put,
                "expiry_year": leg.expiry_year,
                "expiry_month": leg.expiry_month,
                "expiry_day": leg.expiry_day,
                "strike_price": leg.strike_price,
                "osi_key": leg.osi_key,
                "quantity": leg.quantity,
            }
            for leg in ticket.legs
        ],
        "preview_request": ticket.preview_request,
        "market_snapshot": ticket.market_snapshot,
        "pricing": ticket.pricing.model_dump(),
        "warnings": ticket.warnings,
    }
    summary_notice = (
        "Dry-run only. This is the exact execution package the app would use for a future place-order step."
        if ticket.execution_intent.ready_for_execution
        else "Execution intent is not prepared yet. Prepare preview request first to populate the dry-run package."
    )
    mock_execute_gate = _mock_execute_gate(ticket)
    mock_execute_ready = all(mock_execute_gate.values())
    mock_execute_checks = "".join(
        (
            f"<li><strong>{escape(label.replace('_', ' ').title())}:</strong> "
            f"{'yes' if ready else 'no'}</li>"
        )
        for label, ready in mock_execute_gate.items()
    )
    mock_execute_form = f"""
      <form method="post" action="/tickets/{escape(ticket.ticket_id)}/mock-execute">
        <button type="submit"{'' if mock_execute_ready else ' disabled'}>
          {'Run Mock Execute Review' if mock_execute_ready else 'Execute Locked'}
        </button>
      </form>
    """
    mock_execute_hint = (
        "Mock only. This records the final review step but does not send any live order to E*TRADE."
        if mock_execute_ready
        else "This stays locked until execution intent is prepared, user confirmation is on, and all legs have matched broker contracts."
    )
    execution_service_form = f"""
      <form method="post" action="/tickets/{escape(ticket.ticket_id)}/submit-execution">
        <button type="submit"{'' if live_readiness['ready'] else ' disabled'}>
          {escape(execution_service_button_label)}
        </button>
      </form>
    """

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Ticket {escape(ticket.ticket_id)}</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: linear-gradient(180deg, #f7f3ea 0%, #efe6d5 100%);
      color: #1f2933;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .message {{
      border-left: 4px solid #0f766e;
      padding: 12px 14px;
      margin-bottom: 18px;
      background: rgba(15, 118, 110, 0.08);
    }}
    .error {{
      border-left-color: #a11d33;
      background: rgba(161, 29, 51, 0.08);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    .panel {{
      background: #fffaf1;
      border: 1px solid #d8c9aa;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(63, 46, 25, 0.08);
    }}
    h1, h2 {{
      font-family: Georgia, "Times New Roman", serif;
      margin-top: 0;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8f4ec;
      border: 1px solid #d8c9aa;
      border-radius: 12px;
      padding: 12px;
    }}
    .prep-form {{
      display: grid;
      gap: 8px;
    }}
    input {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #d8c9aa;
      border-radius: 10px;
      padding: 8px 10px;
      margin-top: 4px;
    }}
    button {{
      border: 0;
      background: #0f766e;
      color: white;
      padding: 10px 12px;
      border-radius: 10px;
      cursor: pointer;
    }}
    a {{
      color: #8b5e34;
    }}
    {_app_nav_styles()}
  </style>
</head>
<body>
  <main>
    {_render_app_nav("tickets")}
    <h1>{escape(ticket.underlier)} Ticket</h1>
    <p><a href="/tickets/{escape(ticket.ticket_id)}.json">Open Ticket JSON</a></p>
    {f'<div class="message">{escape(message)}</div>' if message else ''}
    {f'<div class="message error">{escape(error)}</div>' if error else ''}
    {sandbox_banner}
    <div class="grid">
      <section class="panel">
        <h2>Summary</h2>
        <p><strong>Ticket:</strong> {escape(ticket.ticket_id)}</p>
        <p><strong>Status:</strong> {escape(ticket.status)}</p>
        <p><strong>Strategy:</strong> {escape(ticket.strategy or 'n/a')}</p>
        <p><strong>Source:</strong> {escape(ticket.source)}</p>
        <p><strong>Account:</strong> {escape(ticket.broker.account_id_key or 'unset')}</p>
        <p><strong>Order Type:</strong> {escape(ticket.broker.order_type)}</p>
        <p><strong>Preview Attempts:</strong> {ticket.preview_attempts}</p>
        <p><strong>Last Error:</strong> {escape(ticket.last_error or 'none')}</p>
        <p><strong>Status Reason:</strong> {escape(ticket.status_reason or 'n/a')}</p>
        <p><strong>Retry Timing:</strong> {escape(cooldown_message or 'ready now')}</p>
        {retry_form}
        {confirm_form}
      </section>
      <section class="panel">
        <h2>Pricing</h2>
        <pre>{escape(_pretty_json(ticket.pricing.model_dump()))}</pre>
      </section>
      <section class="panel">
        <h2>Execution Intent</h2>
        <pre>{escape(_pretty_json(ticket.execution_intent.model_dump()))}</pre>
      </section>
      <section class="panel">
        <h2>Live Execute Readiness</h2>
        <p><strong>Ready:</strong> {escape(str(live_readiness['ready']))}</p>
        <p><strong>Signal Age:</strong> {escape(str(live_readiness['ages']['signal_age']))} / max {escape(str(live_readiness['thresholds']['signal_max_age_minutes']))}m</p>
        <p><strong>Preview Age:</strong> {escape(str(live_readiness['ages']['preview_age']))} / max {escape(str(live_readiness['thresholds']['preview_max_age_minutes']))}m</p>
        <p><strong>Snapshot Age:</strong> {escape(str(live_readiness['ages']['snapshot_age']))} / max {escape(str(live_readiness['thresholds']['snapshot_max_age_minutes']))}m</p>
        <p><strong>Checks:</strong></p>
        <ul>{live_execute_checks}</ul>
        <p><strong>Blockers:</strong></p>
        <ul>{live_execute_blockers}</ul>
      </section>
      <section class="panel">
        <h2>Dry-Run Execution Summary</h2>
        <p>{escape(summary_notice)}</p>
        <pre>{escape(_pretty_json(execution_summary))}</pre>
      </section>
      <section class="panel">
        <h2>Execute (Mock)</h2>
        <p>{escape(mock_execute_hint)}</p>
        <ul>{mock_execute_checks}</ul>
        {mock_execute_form}
      </section>
      <section class="panel">
        <h2>Execution Service</h2>
        <p><strong>Mode:</strong> {escape(current_execution_mode)}</p>
        <p><strong>Last Result:</strong> {escape(execution_service_message)}</p>
        <p>This is the place-order service layer. It builds the exact `PlaceOrderRequest` payload and stays dry-run by default.</p>
        {execution_service_form}
        <pre>{escape(_pretty_json(execution_service_payload))}</pre>
      </section>
      {prep_form}
      {suggestions_html}
      <section class="panel">
        <h2>Legs</h2>
        <pre>{escape(_pretty_json([leg.model_dump() for leg in ticket.legs]))}</pre>
      </section>
      <section class="panel">
        <h2>Warnings</h2>
        <pre>{escape(_pretty_json(ticket.warnings))}</pre>
      </section>
      <section class="panel">
        <h2>Activity Log</h2>
        <ul>{audit_items}</ul>
      </section>
      <section class="panel">
        <h2>Broker Suggestions</h2>
        <pre>{escape(_pretty_json(ticket.broker_suggestions))}</pre>
      </section>
      <section class="panel">
        <h2>Market Snapshot</h2>
        <pre>{escape(_pretty_json(ticket.market_snapshot))}</pre>
      </section>
      <section class="panel">
        <h2>Preview Request</h2>
        <pre>{escape(_pretty_json(ticket.preview_request))}</pre>
      </section>
      <section class="panel">
        <h2>Preview Response</h2>
        <pre>{escape(_pretty_json(ticket.preview_response))}</pre>
      </section>
      <section class="panel">
        <h2>Place Order Request</h2>
        <pre>{escape(_pretty_json(place_request_preview))}</pre>
      </section>
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _ticket_from_preview_request(
    *,
    account_id_key: str,
    preview_order_request: dict[str, object],
) -> TradeTicketV1:
    ticket = build_trade_ticket_from_preview(
        account_id_key=account_id_key,
        preview_order_request={"PreviewOrderRequest": preview_order_request},
        preview_response={},
        strategy="manual_preview",
        source="manual",
    )
    return ticket.model_copy(
        update={
            "status": "preview_pending",
            "preview_attempts": 0,
            "last_preview_at": None,
            "last_error": None,
            "warnings": [],
        }
    )


def _retry_preview_for_ticket(ticket_id: str):
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_id}")
    ticket, remapped_account = _remap_signal_ticket_account_if_needed(ticket)
    if remapped_account:
        save_ticket(ticket)
        _audit_ticket(
            ticket,
            category="ticket",
            action="remap_account",
            summary="Signal ticket account key remapped to the current default before retry preview.",
            actor="system",
            details={"account_id_key": ticket.broker.account_id_key},
        )
    if not ticket.broker.account_id_key:
        raise RuntimeError("Ticket has no broker account id key")
    if not ticket.preview_request:
        raise RuntimeError("Ticket has no stored preview request")
    next_retry = _parse_iso_dt(ticket.next_retry_at)
    if next_retry is not None and next_retry > datetime.now(timezone.utc):
        raise RuntimeError(
            ticket.status_reason
            or f"Retry is temporarily paused until {ticket.next_retry_at}."
        )

    # Rebuild the preview request from current ticket state so older tickets
    # pick up newer payload fixes automatically.
    ticket = rebuild_ticket_preview_request(ticket)
    save_ticket(ticket)
    _audit_ticket(
        ticket,
        category="preview",
        action="rebuild_request",
        summary="Preview request rebuilt from the latest ticket state.",
        details={
            "order_type": ticket.broker.order_type,
            "price_type": ticket.pricing.price_type,
        },
    )

    pending = mark_ticket_preview_pending(ticket)
    save_ticket(pending)
    _audit_ticket(
        pending,
        category="preview",
        action="submit_preview",
        summary="Preview request submitted to E*TRADE.",
        details={
            "preview_attempts": pending.preview_attempts,
        },
    )
    client = load_client()
    try:
        preview = client.preview_order(
            pending.broker.account_id_key,
            pending.preview_request,
        )
        refreshed = mark_ticket_previewed(pending, preview_response=preview)
        save_ticket(refreshed)
        _audit_ticket(
            refreshed,
            category="preview",
            action="preview_succeeded",
            summary="E*TRADE preview succeeded for the ticket.",
            details={
                "preview_id": refreshed.broker.preview_id,
                "preview_attempts": refreshed.preview_attempts,
            },
        )
        return refreshed
    except Exception as exc:
        normalized_error = _normalize_etrade_error(str(exc))
        failed = mark_ticket_preview_failed(
            pending,
            error_message=normalized_error,
        )
        save_ticket(failed)
        _audit_ticket(
            failed,
            category="preview",
            action="preview_failed",
            summary="E*TRADE preview failed for the ticket.",
            details={
                "error": normalized_error,
                "preview_attempts": failed.preview_attempts,
            },
        )
        raise RuntimeError(normalized_error) from exc


def _prepare_preview_for_ticket(
    ticket_id: str,
    *,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    quantity: int,
    limit_price: float,
) -> TradeTicketV1:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_id}")
    ticket, remapped_account = _remap_signal_ticket_account_if_needed(ticket)
    if remapped_account:
        save_ticket(ticket)
        _audit_ticket(
            ticket,
            category="ticket",
            action="remap_account",
            summary="Signal ticket account key remapped to the current default before preparing preview.",
            actor="system",
            details={"account_id_key": ticket.broker.account_id_key},
        )
    prepared = prepare_ticket_for_preview(
        ticket,
        expiry_year=expiry_year,
        expiry_month=expiry_month,
        expiry_day=expiry_day,
        quantity=quantity,
        limit_price=limit_price,
    )
    save_ticket(prepared)
    _audit_ticket(
        prepared,
        category="ticket",
        action="prepare_preview",
        summary="Prepared a broker preview request from the ticket detail page.",
        actor="user",
        details={
            "quantity": quantity,
            "limit_price": limit_price,
            "expiry_year": expiry_year,
            "expiry_month": expiry_month,
            "expiry_day": expiry_day,
        },
    )
    return prepared


def _set_ticket_execution_confirmation(
    ticket_id: str,
    *,
    confirmed: bool,
) -> TradeTicketV1:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_id}")
    notes = [
        note
        for note in ticket.execution_intent.notes
        if note not in ("User confirmed execution intent.", "User removed execution confirmation.")
    ]
    notes.append(
        "User confirmed execution intent."
        if confirmed
        else "User removed execution confirmation."
    )
    updated = ticket.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "execution_intent": ticket.execution_intent.model_copy(
                update={
                    "user_confirmed": confirmed,
                    "notes": notes,
                }
            ),
        }
    )
    save_ticket(updated)
    _audit_ticket(
        updated,
        category="execution",
        action=("confirm_intent" if confirmed else "remove_confirmation"),
        summary=(
            "Execution intent confirmed for later review."
            if confirmed
            else "Execution confirmation was removed."
        ),
        actor="user",
        details={
            "user_confirmed": confirmed,
            "ready_for_execution": updated.execution_intent.ready_for_execution,
        },
    )
    return updated


def _run_mock_execute_for_ticket(ticket_id: str) -> TradeTicketV1:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_id}")
    gate = _mock_execute_gate(ticket)
    blockers = [label.replace("_", " ") for label, ready in gate.items() if not ready]
    if blockers:
        raise RuntimeError(
            "Mock execute is locked until these gates pass: "
            + ", ".join(blockers)
            + "."
        )
    timestamp = datetime.now(timezone.utc).isoformat()
    notes = list(ticket.execution_intent.notes)
    notes.append(
        f"Mock execute reviewed at {timestamp}. No live order was sent."
    )
    updated = ticket.model_copy(
        update={
            "updated_at": timestamp,
            "execution_intent": ticket.execution_intent.model_copy(
                update={"notes": notes}
            ),
        }
    )
    save_ticket(updated)
    _audit_ticket(
        updated,
        category="execution",
        action="mock_execute_reviewed",
        summary="Mock execute review completed without sending a live order.",
        actor="user",
        details={
            "gates": gate,
        },
    )
    return updated


def _submit_ticket_execution(ticket_id: str) -> TradeTicketV1:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {ticket_id}")
    readiness = _live_execute_readiness(ticket)
    if not readiness["ready"]:
        raise RuntimeError(
            "Execution submission is locked until these blockers clear: "
            + ", ".join(str(item) for item in readiness["blockers"])
            + "."
        )

    result = submit_execution_package(ticket)
    place_request = result.get("request", {})
    next_status = "submitted" if result.get("submitted") else ticket.status
    updated = ticket.model_copy(
        update={
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": next_status,
            "status_reason": str(result.get("message") or ticket.status_reason or ""),
            "place_order_request": place_request if isinstance(place_request, dict) else {},
            "execution_record": result,
        }
    )
    save_ticket(updated)
    _audit_ticket(
        updated,
        category="execution",
        action=(
            "execution_submitted"
            if result.get("submitted")
            else "execution_package_built"
        ),
        summary=str(result.get("message") or "Execution service processed the ticket."),
        actor="user",
        details={
            "mode": result.get("mode"),
            "submitted": bool(result.get("submitted")),
        },
    )
    return updated


log = _setup_logging()
ENGINE_INGEST_SECRET = _load_engine_ingest_secret()
log.info(
    "Web config: ingest_secret=%s",
    ("set" if ENGINE_INGEST_SECRET else "empty"),
)
log.info("Web config: dotenv_path=%s", _dotenv_path or "not_found")
log.info(
    "Web config: slack_webhook=%s",
    ("set" if get_slack_webhook_url() else "empty"),
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return _render_home(_build_ops_snapshot())


@app.get("/ops/status", response_class=HTMLResponse)
async def ops_status(viewer: str = Depends(_require_admin)) -> HTMLResponse:
    return _render_ops_status(_build_ops_snapshot(), viewer=viewer)


@app.get("/ops/status.json")
async def ops_status_json(_: str = Depends(_require_admin)) -> dict[str, object]:
    return {"ok": True, "status": _build_ops_snapshot()}


@app.get("/broker/etrade", response_class=HTMLResponse)
async def etrade_console() -> HTMLResponse:
    return _render_etrade_console()


@app.get("/tickets", response_class=HTMLResponse)
async def ticket_queue() -> HTMLResponse:
    return _render_ticket_queue()


@app.post("/tickets/clear-all", response_class=HTMLResponse)
async def clear_all_tickets() -> HTMLResponse:
    try:
        counts = _clear_ticket_runtime_state(include_pine_states=False)
        return _render_ticket_queue(
            message=(
                "Cleared "
                f"{counts['tickets']} tickets and {counts['audit_logs']} audit logs."
            )
        )
    except Exception as exc:
        log.exception("ticket clear-all failed")
        return _render_ticket_queue(
            error=f"Failed to clear ticket queue: {exc}",
        )


@app.post("/tickets/reset-day", response_class=HTMLResponse)
async def reset_ticket_day() -> HTMLResponse:
    try:
        counts = _clear_ticket_runtime_state(include_pine_states=True)
        return _render_ticket_queue(
            message=(
                "Reset runtime state. Cleared "
                f"{counts['tickets']} tickets, {counts['audit_logs']} audit logs, "
                f"and {counts['pine_states']} Pine bridge states."
            )
        )
    except Exception as exc:
        log.exception("ticket reset-day failed")
        return _render_ticket_queue(
            error=f"Failed to reset runtime state: {exc}",
        )


@app.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: str) -> HTMLResponse:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return _render_ticket_detail(ticket)


@app.get("/tickets/{ticket_id}.json")
async def ticket_detail_json(ticket_id: str) -> dict[str, object]:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return {"ok": True, "ticket": ticket.model_dump()}


@app.post("/tickets/{ticket_id}/prepare-preview", response_class=HTMLResponse)
async def prepare_ticket_preview(
    ticket_id: str,
    expiry_year: int = Form(...),
    expiry_month: int = Form(...),
    expiry_day: int = Form(...),
    quantity: int = Form(...),
    limit_price: float = Form(...),
) -> HTMLResponse:
    try:
        prepared = _prepare_preview_for_ticket(
            ticket_id,
            expiry_year=expiry_year,
            expiry_month=expiry_month,
            expiry_day=expiry_day,
            quantity=quantity,
            limit_price=limit_price,
        )
        return _render_ticket_detail(
            prepared,
            message=f"Prepared preview request for {prepared.ticket_id}. You can retry preview now.",
        )
    except Exception as exc:
        log.exception("prepare preview failed")
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Failed to prepare preview for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Failed to prepare preview for {ticket_id}: {exc}",
        )


@app.post("/tickets/{ticket_id}/suggest-expiries", response_class=HTMLResponse)
async def suggest_ticket_expiries(ticket_id: str) -> HTMLResponse:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        return _render_ticket_queue(error=f"Ticket not found: {ticket_id}")
    try:
        client = load_client()
        payload = client.get_option_expire_dates(ticket.underlier)
        return _render_ticket_detail(
            ticket,
            message=f"Loaded expiry suggestions for {ticket.underlier}.",
            expiry_suggestions=payload,
        )
    except Exception as exc:
        log.exception("ticket expiry suggestion failed")
        return _render_ticket_detail(
            ticket,
            error=f"Failed to load expiry suggestions for {ticket.underlier}: {exc}",
        )


@app.post("/tickets/{ticket_id}/auto-suggest-contracts", response_class=HTMLResponse)
async def auto_suggest_ticket_contracts(ticket_id: str) -> HTMLResponse:
    ticket = load_ticket(ticket_id)
    if ticket is None:
        return _render_ticket_queue(error=f"Ticket not found: {ticket_id}")
    try:
        enriched, payload = _suggest_contracts_for_ticket(ticket)
        matched = payload.get("matched_contracts")
        if matched:
            return _render_ticket_detail(
                enriched,
                message=f"Auto-suggested contracts for {enriched.underlier}. Review the prefilled expiry, then prepare preview.",
                expiry_suggestions=payload,
            )
        return _render_ticket_detail(
            ticket,
            error=f"No exact contract matches found for {ticket.underlier} with the current strikes.",
            expiry_suggestions=payload,
        )
    except Exception as exc:
        log.exception("ticket contract auto-suggest failed")
        return _render_ticket_detail(
            ticket,
            error=f"Failed to auto-suggest contracts for {ticket.underlier}: {exc}",
        )


@app.get("/broker/etrade/status")
async def etrade_status() -> dict[str, object]:
    token = load_access_token()
    pending = load_pending_request_token()
    api_config = load_api_config()
    token_status = token_expiry_status(token)
    return {
        "ok": True,
        "connected": token is not None,
        "pending_request_token": pending is not None,
        "token_path": str(get_session_file_path()),
        "environment": api_config.environment,
        "base_url": api_config.base_url,
        "token_status": token_status,
    }


@app.get("/broker/etrade/connect")
async def etrade_connect() -> dict[str, object]:
    try:
        config = load_consumer_config()
        token = request_token(config)
        save_pending_request_token(token)
        return {
            "ok": True,
            "authorize_url": build_authorize_url(config, token),
            "callback_mode": config.callback_url,
        }
    except Exception as exc:
        log.exception("etrade connect failed")
        raise HTTPException(500, f"E*TRADE connect failed: {exc}") from exc


@app.post("/broker/etrade/connect/start")
async def etrade_connect_start() -> RedirectResponse:
    config = load_consumer_config()
    token = request_token(config)
    save_pending_request_token(token)
    return RedirectResponse(
        url=build_authorize_url(config, token),
        status_code=303,
    )


@app.get("/broker/etrade/callback")
async def etrade_callback(oauth_verifier: str | None = None) -> dict[str, object]:
    if not oauth_verifier:
        raise HTTPException(400, "Missing oauth_verifier")

    request_tok = load_pending_request_token()
    if request_tok is None:
        raise HTTPException(400, "No pending E*TRADE request token found")

    try:
        config = load_consumer_config()
        access_token = exchange_access_token(config, request_tok, oauth_verifier)
        save_access_token(access_token)
        clear_pending_request_token()
        return {
            "ok": True,
            "message": "E*TRADE access token stored",
            "token_path": str(get_session_file_path()),
        }
    except Exception as exc:
        log.exception("etrade callback failed")
        raise HTTPException(500, f"E*TRADE callback failed: {exc}") from exc


@app.post("/broker/etrade/complete")
async def etrade_complete(oauth_verifier: str = Form(...)) -> HTMLResponse:
    request_tok = load_pending_request_token()
    if request_tok is None:
        return _render_etrade_console(
            error="No pending E*TRADE request token found. Start the authorization flow first."
        )

    try:
        config = load_consumer_config()
        access_token = exchange_access_token(config, request_tok, oauth_verifier.strip())
        save_access_token(access_token)
        clear_pending_request_token()
        return _render_etrade_console(message="E*TRADE access token stored successfully.")
    except Exception as exc:
        log.exception("etrade complete failed")
        return _render_etrade_console(error=f"E*TRADE verifier exchange failed: {exc}")


@app.post("/broker/etrade/renew")
async def etrade_renew() -> dict[str, object]:
    token = load_access_token()
    if token is None:
        raise HTTPException(400, "No stored E*TRADE access token found")

    try:
        message = renew_stored_access_token(token)
        return {"ok": True, "message": message}
    except Exception as exc:
        log.exception("etrade renew failed")
        raise HTTPException(500, f"E*TRADE renew failed: {exc}") from exc


@app.post("/broker/etrade/renew/browser")
async def etrade_renew_browser() -> HTMLResponse:
    token = load_access_token()
    if token is None:
        return _render_etrade_console(error="No stored E*TRADE access token found.")

    try:
        message = renew_stored_access_token(token)
        return _render_etrade_console(message=f"Renew result: {message}")
    except Exception as exc:
        log.exception("etrade renew browser failed")
        return _render_etrade_console(error=f"E*TRADE renew failed: {exc}")


@app.get("/broker/etrade/accounts")
async def etrade_accounts() -> dict[str, object]:
    try:
        client = load_client()
        payload = client.list_accounts()
        return {"ok": True, "accounts": payload}
    except Exception as exc:
        log.exception("etrade accounts failed")
        raise HTTPException(500, f"E*TRADE accounts failed: {exc}") from exc


@app.post("/broker/etrade/console/accounts", response_class=HTMLResponse)
async def etrade_console_accounts() -> HTMLResponse:
    try:
        client = load_client()
        payload = client.list_accounts()
        return _render_etrade_console(
            message="Accounts loaded successfully.",
            accounts_payload=payload,
        )
    except Exception as exc:
        log.exception("etrade console accounts failed")
        return _render_etrade_console(error=f"E*TRADE accounts failed: {exc}")


@app.get("/broker/etrade/accounts/{account_id_key}/balance")
async def etrade_balance(
    account_id_key: str,
    account_type: str | None = None,
) -> dict[str, object]:
    try:
        client = load_client()
        payload = client.get_balance(account_id_key, account_type=account_type)
        return {"ok": True, "balance": payload}
    except Exception as exc:
        log.exception("etrade balance failed")
        raise HTTPException(500, f"E*TRADE balance failed: {exc}") from exc


@app.post("/broker/etrade/console/balance", response_class=HTMLResponse)
async def etrade_console_balance(account_id_key: str = Form(...)) -> HTMLResponse:
    try:
        client = load_client()
        accounts_payload = client.list_accounts()
        balance_payload = client.get_balance(account_id_key.strip())
        return _render_etrade_console(
            message=f"Balance loaded for {account_id_key.strip()}.",
            accounts_payload=accounts_payload,
            balance_payload=balance_payload,
            selected_account_id_key=account_id_key.strip(),
        )
    except Exception as exc:
        log.exception("etrade console balance failed")
        return _render_etrade_console(error=f"E*TRADE balance failed: {exc}")


@app.post("/broker/etrade/orders/preview")
async def etrade_preview_order(request: Request) -> dict[str, object]:
    payload = await request.json()
    account_id_key = str(payload.get("accountIdKey", "")).strip()
    preview_order_request = payload.get("PreviewOrderRequest")
    if not account_id_key:
        raise HTTPException(400, "Missing accountIdKey")
    if not isinstance(preview_order_request, dict):
        raise HTTPException(400, "Missing PreviewOrderRequest object")

    try:
        ticket = _ticket_from_preview_request(
            account_id_key=account_id_key,
            preview_order_request=preview_order_request,
        )
        save_ticket(ticket)
        _audit_ticket(
            ticket,
            category="ticket",
            action="manual_ticket_created",
            summary="Manual preview ticket created from an API preview request.",
            details={"account_id_key": account_id_key},
        )
        client = load_client()
        pending = mark_ticket_preview_pending(ticket)
        save_ticket(pending)
        _audit_ticket(
            pending,
            category="preview",
            action="submit_preview",
            summary="Preview request submitted to E*TRADE.",
            details={"preview_attempts": pending.preview_attempts},
        )
        preview = client.preview_order(account_id_key, preview_order_request)
        final_ticket = mark_ticket_previewed(pending, preview_response=preview)
        save_ticket(final_ticket)
        _audit_ticket(
            final_ticket,
            category="preview",
            action="preview_succeeded",
            summary="E*TRADE preview succeeded for the manual ticket.",
            details={"preview_id": final_ticket.broker.preview_id},
        )
        return {"ok": True, "preview": preview, "ticket": final_ticket.model_dump()}
    except Exception as exc:
        log.exception("etrade preview failed")
        normalized_error = _normalize_etrade_error(str(exc))
        if "pending" in locals():
            failed = mark_ticket_preview_failed(pending, error_message=normalized_error)
            save_ticket(failed)
            _audit_ticket(
                failed,
                category="preview",
                action="preview_failed",
                summary="E*TRADE preview failed for the manual ticket.",
                details={"error": normalized_error},
            )
        elif "ticket" in locals():
            failed = mark_ticket_preview_failed(ticket, error_message=normalized_error)
            save_ticket(failed)
            _audit_ticket(
                failed,
                category="preview",
                action="preview_failed",
                summary="E*TRADE preview failed before the ticket reached preview-pending.",
                details={"error": normalized_error},
            )
        raise HTTPException(500, f"E*TRADE preview failed: {normalized_error}") from exc


@app.post("/broker/etrade/console/preview", response_class=HTMLResponse)
async def etrade_console_preview(
    account_id_key: str = Form(...),
    preview_payload_json: str = Form(...),
) -> HTMLResponse:
    account_id_key = account_id_key.strip()
    try:
        parsed = json.loads(preview_payload_json)
    except json.JSONDecodeError as exc:
        return _render_etrade_console(
            error=f"Preview payload is not valid JSON: {exc}",
            preview_request_text=preview_payload_json,
            selected_account_id_key=account_id_key,
        )

    preview_order_request = parsed.get("PreviewOrderRequest")
    if not isinstance(preview_order_request, dict):
        return _render_etrade_console(
            error="Preview payload must contain a top-level PreviewOrderRequest object.",
            preview_request_text=preview_payload_json,
            selected_account_id_key=account_id_key,
        )

    try:
        client = load_client()
        accounts_payload = client.list_accounts()
        ticket = _ticket_from_preview_request(
            account_id_key=account_id_key,
            preview_order_request=preview_order_request,
        )
        save_ticket(ticket)
        _audit_ticket(
            ticket,
            category="ticket",
            action="manual_ticket_created",
            summary="Manual preview ticket created from the browser console.",
            actor="user",
            details={"account_id_key": account_id_key},
        )
        pending = mark_ticket_preview_pending(ticket)
        save_ticket(pending)
        _audit_ticket(
            pending,
            category="preview",
            action="submit_preview",
            summary="Preview request submitted to E*TRADE from the browser console.",
            actor="user",
            details={"preview_attempts": pending.preview_attempts},
        )
        preview_payload = client.preview_order(account_id_key, preview_order_request)
        final_ticket = mark_ticket_previewed(pending, preview_response=preview_payload)
        save_ticket(final_ticket)
        _audit_ticket(
            final_ticket,
            category="preview",
            action="preview_succeeded",
            summary="E*TRADE preview succeeded for the browser-created ticket.",
            actor="user",
            details={"preview_id": final_ticket.broker.preview_id},
        )
        return _render_etrade_console(
            message=f"Preview returned for {account_id_key}. Ticket {final_ticket.ticket_id} saved.",
            accounts_payload=accounts_payload,
            preview_payload=preview_payload,
            preview_request_text=preview_payload_json,
            selected_account_id_key=account_id_key,
        )
    except Exception as exc:
        log.exception("etrade console preview failed")
        normalized_error = _normalize_etrade_error(str(exc))
        if "pending" in locals():
            failed = mark_ticket_preview_failed(pending, error_message=normalized_error)
            save_ticket(failed)
            _audit_ticket(
                failed,
                category="preview",
                action="preview_failed",
                summary="E*TRADE preview failed for the browser-created ticket.",
                actor="user",
                details={"error": normalized_error},
            )
        elif "ticket" in locals():
            failed = mark_ticket_preview_failed(ticket, error_message=normalized_error)
            save_ticket(failed)
            _audit_ticket(
                failed,
                category="preview",
                action="preview_failed",
                summary="E*TRADE preview failed before the browser ticket reached preview-pending.",
                actor="user",
                details={"error": normalized_error},
            )
        return _render_etrade_console(
            error=f"E*TRADE preview failed: {normalized_error}",
            preview_request_text=preview_payload_json,
            selected_account_id_key=account_id_key,
        )


@app.post("/tickets/{ticket_id}/retry-preview", response_class=HTMLResponse)
async def retry_ticket_preview(ticket_id: str) -> HTMLResponse:
    try:
        refreshed = _retry_preview_for_ticket(ticket_id)
        return _render_ticket_detail(
            refreshed,
            message=f"Preview succeeded for {refreshed.ticket_id}.",
        )
    except Exception as exc:
        log.exception("ticket preview retry failed")
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Preview retry failed for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Preview retry failed for {ticket_id}: {exc}",
        )


@app.post("/tickets/{ticket_id}/confirm-intent", response_class=HTMLResponse)
async def confirm_ticket_intent(ticket_id: str) -> HTMLResponse:
    try:
        ticket = _set_ticket_execution_confirmation(ticket_id, confirmed=True)
        return _render_ticket_detail(
            ticket,
            message=f"Execution intent confirmed for {ticket.ticket_id}.",
        )
    except Exception as exc:
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Failed to confirm execution intent for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Failed to confirm execution intent for {ticket_id}: {exc}",
        )


@app.post("/tickets/{ticket_id}/unconfirm-intent", response_class=HTMLResponse)
async def unconfirm_ticket_intent(ticket_id: str) -> HTMLResponse:
    try:
        ticket = _set_ticket_execution_confirmation(ticket_id, confirmed=False)
        return _render_ticket_detail(
            ticket,
            message=f"Execution confirmation removed for {ticket.ticket_id}.",
        )
    except Exception as exc:
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Failed to remove execution confirmation for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Failed to remove execution confirmation for {ticket_id}: {exc}",
        )


@app.post("/tickets/{ticket_id}/mock-execute", response_class=HTMLResponse)
async def mock_execute_ticket(ticket_id: str) -> HTMLResponse:
    try:
        ticket = _run_mock_execute_for_ticket(ticket_id)
        return _render_ticket_detail(
            ticket,
            message=(
                f"Mock execute reviewed for {ticket.ticket_id}. "
                "No live order was sent."
            ),
        )
    except Exception as exc:
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Mock execute failed for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Mock execute failed for {ticket_id}: {exc}",
        )


@app.post("/tickets/{ticket_id}/submit-execution", response_class=HTMLResponse)
async def submit_ticket_execution(ticket_id: str) -> HTMLResponse:
    try:
        ticket = _submit_ticket_execution(ticket_id)
        return _render_ticket_detail(
            ticket,
            message=str(
                ticket.execution_record.get("message")
                or "Execution service completed."
            ),
        )
    except Exception as exc:
        ticket = load_ticket(ticket_id)
        if ticket is None:
            return _render_ticket_queue(
                error=f"Execution submit failed for {ticket_id}: {exc}",
            )
        return _render_ticket_detail(
            ticket,
            error=f"Execution submit failed for {ticket_id}: {exc}",
        )


@app.post("/engine/ingest")
async def engine_ingest(
    request: Request,
    x_engine_timestamp: str | None = Header(default=None),
    x_engine_signature: str | None = Header(default=None),
):
    body = await request.body()
    if not x_engine_timestamp or not x_engine_signature:
        log.warning("ingest rejected: missing signature headers")
        raise HTTPException(401, "Missing engine signature headers")

    if ENGINE_INGEST_SECRET:
        ok = verify_body_v1(
            ENGINE_INGEST_SECRET, body, x_engine_timestamp, x_engine_signature
        )
        if not ok:
            log.warning("ingest rejected: bad signature")
            raise HTTPException(401, "Bad engine signature")

    payload = await request.json()
    fact = EngineFactV1.model_validate(payload)

    fact_dict = fact.model_dump()
    kind = fact_dict.get("kind")
    log.info(
        "ingest ok kind=%s run_id=%s seq=%s",
        kind,
        fact_dict.get("run_id"),
        fact_dict.get("seq"),
    )
    if kind in ("ENGINE_EVENT", "STRATEGY_SIGNAL"):
        msg = format_fact_message(fact_dict)
        try:
            ok = send_slack_message(msg)
            if not ok:
                log.warning("slack send failed (non-2xx or missing webhook)")
        except Exception:
            log.exception("slack send exception")

    if kind == "STRATEGY_SIGNAL":
        account_id_key = _default_account_id_key()
        if account_id_key:
            try:
                client_order_id = f"signal-{fact.run_id}-{fact.seq}"
                ticket = build_trade_ticket_from_signal(
                    fact=fact_dict,
                    account_id_key=account_id_key,
                    client_order_id=client_order_id,
                )
                ticket = _auto_enrich_signal_ticket(ticket)
                save_ticket(ticket)
                _audit_ticket(
                    ticket,
                    category="ticket",
                    action="signal_ticket_created",
                    summary="Signal ingest created or refreshed a trade ticket.",
                    details={
                        "run_id": fact.run_id,
                        "seq": fact.seq,
                        "strategy": ticket.strategy,
                    },
                )
            except Exception:
                log.exception("signal ticket creation failed")
        else:
            log.info("signal ticket skipped: ETRADE_DEFAULT_ACCOUNT_ID_KEY not set")

    return {"ok": True, "run_id": fact.run_id, "seq": fact.seq}


@app.post("/webhooks/tradingview")
async def tradingview_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    if not body:
        raise HTTPException(400, "TradingView webhook body is empty.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(400, f"TradingView webhook must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "TradingView webhook payload must be a JSON object.")

    # Hermes can still emit its legacy JSON alerts alongside the bridge payloads.
    # Those legacy payloads are useful for human monitoring but they do not match
    # the Pine bridge schema, so we acknowledge and skip them here instead of
    # returning an error to TradingView.
    if (
        "tag" in payload
        and "strategy_name" not in payload
        and "position_key" not in payload
    ):
        return JSONResponse(
            {
                "ok": True,
                "skipped": True,
                "reason": "non_bridge_hermes_payload",
                "event": payload.get("event"),
                "tag": payload.get("tag"),
            }
        )

    try:
        result = process_pine_alert(payload)
    except Exception as exc:
        log.exception("tradingview webhook failed")
        message = str(exc)
        lowered = message.lower()
        status_code = 500
        if "secret mismatch" in lowered:
            status_code = 401
        elif (
            "missing" in lowered
            or "must be" in lowered
            or "unsupported" in lowered
            or "no active entry ticket" in lowered
        ):
            status_code = 400
        raise HTTPException(status_code, f"TradingView webhook failed: {message}") from exc

    state = result.get("state")
    if isinstance(state, dict):
        log.info(
            "tradingview webhook ok strategy=%s event=%s state_id=%s duplicate=%s",
            state.get("strategy_name"),
            state.get("last_event_type"),
            state.get("state_id"),
            bool(result.get("duplicate")),
        )
    return JSONResponse(result)


@app.get("/pine-bridge/states")
async def pine_bridge_states() -> dict[str, object]:
    states = list_pine_states()
    return {
        "ok": True,
        "count": len(states),
        "states": [state.model_dump() for state in states],
    }


@app.get("/pine-bridge", response_class=HTMLResponse)
async def pine_bridge_overview() -> HTMLResponse:
    return _render_pine_bridge_overview()


@app.get("/pine-bridge/states/{state_id}")
async def pine_bridge_state_detail(state_id: str) -> dict[str, object]:
    state = load_pine_state(state_id)
    if state is None:
        raise HTTPException(404, f"Pine bridge state not found: {state_id}")
    entry_ticket = load_ticket(state.entry_ticket_id) if state.entry_ticket_id else None
    close_ticket = load_ticket(state.close_ticket_id) if state.close_ticket_id else None
    return {
        "ok": True,
        "state": state.model_dump(),
        "entry_ticket": entry_ticket.model_dump() if entry_ticket else None,
        "close_ticket": close_ticket.model_dump() if close_ticket else None,
    }


@app.post("/slack/events")
async def slack_events(request: Request):
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload["challenge"]})

    return {"ok": True}
