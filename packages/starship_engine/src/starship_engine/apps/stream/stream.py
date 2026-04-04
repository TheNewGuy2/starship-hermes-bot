from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from tastytrade import DXLinkStreamer, Session
from tastytrade.dxfeed import Candle, Greeks, Quote
from tastytrade.instruments import get_option_chain
from tastytrade.utils import today_in_new_york

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.stream.symbols import SPX_CHAIN_SYMBOL, SPX_INDEX_SYMBOL

log = get_logger("starship_engine.stream")
NY = ZoneInfo("America/New_York")


@dataclass
class StreamConfig:
    candle_symbol: str = SPX_INDEX_SYMBOL
    quote_symbol: str = SPX_INDEX_SYMBOL
    chain_symbol: str = SPX_CHAIN_SYMBOL
    candle_interval: str = "5m"
    candle_minutes_back: int = 900
    start_time_override: datetime | None = None
    greeks_refresh: float = 0.25
    candles_refresh: float = 0.25
    extended_trading_hours: bool = False
    # Use today's NY session open as the backfill start time (more stable than large minutes_back)
    backfill_today_session_open: bool = True
    # Optional context candles (e.g., /ES)
    context_candle_symbol: str | None = None
    context_candle_interval: str = "5m"
    context_extended_trading_hours: bool = True


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def candle_time_to_utc(t_ms: int) -> datetime:
    """DXFeed Candle.time is epoch milliseconds."""
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)


def today_session_open_utc(now: datetime | None = None) -> datetime:
    """
    Today's NY session open (9:30 ET) as UTC.
    """
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    open_ny = datetime.combine(now_ny.date(), time(9, 30), tzinfo=NY)
    return open_ny.astimezone(timezone.utc)


def _candle_start_time(cfg: StreamConfig) -> datetime:
    if cfg.start_time_override is not None:
        log.info("Candle Start Time (override): %s", cfg.start_time_override)
        return cfg.start_time_override
    if cfg.backfill_today_session_open:
        start = today_session_open_utc()
        log.info("Candle Start Time (today NY session open): %s", start)
        return start

    start = _utc_now() - timedelta(minutes=cfg.candle_minutes_back)
    log.info("Candle Start Time (minutes_back=%d): %s", cfg.candle_minutes_back, start)
    return start


def overnight_start_utc(now: datetime | None = None) -> datetime:
    """
    Prior day 16:00 ET as UTC (Globex evening start).
    """
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    start_ny = datetime.combine(
        now_ny.date() - timedelta(days=1), time(16, 0), tzinfo=NY
    )
    return start_ny.astimezone(timezone.utc)


def _pick_0dte_options_streamer_symbols(
    session: Session,
    chain_symbol: str,
) -> list[str]:
    """
    Pull SPX option chain via REST and return streamer symbols for today's expiration (NY date).
    """
    chain = get_option_chain(session, chain_symbol)
    exp = today_in_new_york()

    options_today = None
    if exp in chain:
        options_today = chain[exp]
    else:
        exp_iso = exp.isoformat()
        if exp_iso in chain:
            options_today = chain[exp_iso]

    if not options_today:
        nearest_key = sorted(chain.keys())[0]
        options_today = chain[nearest_key]

    streamer_symbols = [
        opt.streamer_symbol
        for opt in options_today
        if getattr(opt, "streamer_symbol", None)
    ]
    if not streamer_symbols:
        raise RuntimeError(
            "No option streamer symbols found (check permissions / chain response)."
        )

    return streamer_symbols


async def run_stream(
    session: Session,
    cfg: StreamConfig,
    *,
    on_candle: callable | None = None,
    on_context_candle: callable | None = None,
    on_greeks: callable | None = None,
    on_quote: callable | None = None,
) -> None:
    """
    Connect to DXLink, subscribe to SPX candles + SPX quote + 0DTE SPXW Greeks.
    Calls callbacks when events arrive (optional).
    """
    option_streamer_symbols = _pick_0dte_options_streamer_symbols(
        session,
        cfg.chain_symbol,
    )

    context_symbols: list[str] = []
    if cfg.context_candle_symbol:
        context_symbols.append(cfg.context_candle_symbol)
        if ":" in cfg.context_candle_symbol:
            context_symbols.append(cfg.context_candle_symbol.split(":", 1)[0])

    log.info(
        "Stream starting: candle_sym=%s quote_sym=%s chain_sym=%s interval=%s minutes_back=%d ETH=%s greeks_syms=%d context_sym=%s",
        cfg.candle_symbol,
        cfg.quote_symbol,
        cfg.chain_symbol,
        cfg.candle_interval,
        cfg.candle_minutes_back,
        cfg.extended_trading_hours,
        len(option_streamer_symbols),
        (",".join(context_symbols) if context_symbols else "-"),
    )

    async with DXLinkStreamer(session) as streamer:
        # --- Subscribe: SPX 5m candles ---
        await streamer.subscribe_candle(
            symbols=[cfg.candle_symbol],
            interval=cfg.candle_interval,
            start_time=_candle_start_time(cfg),
            extended_trading_hours=cfg.extended_trading_hours,
            refresh_interval=cfg.candles_refresh,
        )

        # --- Subscribe: quote ---
        await streamer.subscribe(Quote, [cfg.quote_symbol], refresh_interval=0.25)

        # --- Subscribe: Greeks for 0DTE chain ---
        await streamer.subscribe(
            Greeks, option_streamer_symbols, refresh_interval=cfg.greeks_refresh
        )

        # --- Subscribe: optional context candles ---
        if context_symbols:
            await streamer.subscribe_candle(
                symbols=context_symbols,
                interval=cfg.context_candle_interval,
                start_time=overnight_start_utc(),
                extended_trading_hours=cfg.context_extended_trading_hours,
                refresh_interval=cfg.candles_refresh,
            )

        # -----------------------------
        # Liveness + watchdog
        # -----------------------------
        loop = asyncio.get_running_loop()
        last_event_ts = loop.time()
        last_context_ts = loop.time()

        def mark_event(kind: str) -> None:
            nonlocal last_event_ts
            last_event_ts = loop.time()
            log.debug("Event received: %s", kind)

        async def watchdog():
            while True:
                await asyncio.sleep(10)
                idle = loop.time() - last_event_ts
                if idle >= 30:
                    log.warning(
                        "No stream events for %.1fs (symbol/market/permissions?). candle=%s quote=%s greeks=%s",
                        idle,
                        cfg.candle_symbol,
                        cfg.quote_symbol,
                        cfg.chain_symbol,
                    )
                if context_symbols:
                    context_idle = loop.time() - last_context_ts
                    if not seen_context_candle and context_idle >= 60:
                        log.warning(
                            "No context candles for %.0fs (symbol=%s). Check symbol like /ES or /ES:XCME.",
                            context_idle,
                            ",".join(context_symbols),
                        )
                        if other_candle_symbols:
                            log.warning(
                                "Context debug: saw other candle symbols=%s",
                                ",".join(other_candle_symbols.keys()),
                            )

        # First-event flags + counters
        seen_candle = False
        seen_context_candle = False
        seen_any_candle = False
        seen_quote = False
        seen_greeks = False
        candle_count = 0
        context_candle_count = 0
        quote_count = 0
        greeks_count = 0
        other_candle_symbols: dict[str, int] = {}

        async def candle_listener():
            nonlocal \
                seen_candle, \
                seen_context_candle, \
                candle_count, \
                context_candle_count, \
                last_context_ts, \
                seen_any_candle
            async for c in streamer.listen(Candle):
                mark_event("candle")
                sym = getattr(c, "event_symbol", None)
                base_sym = sym.split("{", 1)[0] if isinstance(sym, str) else sym

                if (
                    sym
                    and base_sym not in context_symbols
                    and base_sym != cfg.candle_symbol
                ):
                    if (
                        sym not in other_candle_symbols
                        and len(other_candle_symbols) < 5
                    ):
                        other_candle_symbols[sym] = 0
                    if sym in other_candle_symbols:
                        other_candle_symbols[sym] += 1

                if base_sym in context_symbols:
                    last_context_ts = loop.time()
                    context_candle_count += 1
                    if not seen_context_candle:
                        seen_context_candle = True
                        log.info(
                            "FIRST context candle: sym=%s t=%s o=%s h=%s l=%s c=%s",
                            getattr(c, "event_symbol", None),
                            getattr(c, "time", None),
                            getattr(c, "open", None),
                            getattr(c, "high", None),
                            getattr(c, "low", None),
                            getattr(c, "close", None),
                        )
                    if on_context_candle:
                        on_context_candle(c)
                    continue

                if base_sym != cfg.candle_symbol:
                    continue

                if not seen_any_candle:
                    seen_any_candle = True
                    log.info("FIRST candle event symbol=%s", sym)

                candle_count += 1

                if not seen_candle:
                    seen_candle = True
                    log.info(
                        "FIRST candle: sym=%s t=%s o=%s h=%s l=%s c=%s",
                        getattr(c, "event_symbol", None),
                        getattr(c, "time", None),
                        getattr(c, "open", None),
                        getattr(c, "high", None),
                        getattr(c, "low", None),
                        getattr(c, "close", None),
                    )
                    t = getattr(c, "time", None)
                    if isinstance(t, int):
                        log.info(
                            "FIRST candle time decoded_utc=%s",
                            candle_time_to_utc(t).isoformat(),
                        )

                # Optional: ultra-light heartbeat every 200 candles
                if candle_count % 200 == 0:
                    log.info("Candle events received=%d", candle_count)

                if on_candle:
                    on_candle(c)

        async def quote_listener():
            nonlocal seen_quote, quote_count
            async for q in streamer.listen(Quote):
                mark_event("quote")
                quote_count += 1

                if not seen_quote:
                    seen_quote = True
                    log.info(
                        "FIRST quote: sym=%s bid=%s ask=%s last=%s",
                        getattr(q, "event_symbol", None),
                        getattr(q, "bid_price", None),
                        getattr(q, "ask_price", None),
                        getattr(q, "last_price", None),
                    )

                # Optional: heartbeat every 500 quotes
                if quote_count % 500 == 0:
                    log.info("Quote events received=%d", quote_count)

                if on_quote:
                    on_quote(q)

        async def greeks_listener():
            nonlocal seen_greeks, greeks_count
            async for g in streamer.listen(Greeks):
                mark_event("greeks")
                greeks_count += 1

                if not seen_greeks:
                    seen_greeks = True
                    log.info(
                        "FIRST greeks: sym=%s delta=%s vol=%s iv?=%s",
                        getattr(g, "event_symbol", None),
                        getattr(g, "delta", None),
                        getattr(g, "volatility", None),
                        getattr(g, "iv", None),
                    )

                # Optional: heartbeat every 1000 greeks updates
                if greeks_count % 1000 == 0:
                    log.info("Greeks events received=%d", greeks_count)

                if on_greeks:
                    on_greeks(g)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(candle_listener())
            tg.create_task(quote_listener())
            tg.create_task(greeks_listener())
            tg.create_task(watchdog())
