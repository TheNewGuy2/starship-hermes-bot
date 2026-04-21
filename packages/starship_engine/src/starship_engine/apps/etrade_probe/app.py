from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from starship_shared.enums import FactKind
from starship_shared.options import compute_iv_0dte_band
from starship_shared.schemas import EngineFactV1, MarketRef

from starship_engine.apps.comms.publishers import (
    FanoutPublisher,
    HttpPublisher,
    JsonlPublisher,
    SlackFactPublisher,
)
from starship_engine.apps.etrade_probe.client import (
    ETradeProbeClient,
    load_probe_config,
)
from starship_engine.condor import Condor, pick_condor_20delta_20wide


def _central_tz():
    try:
        return ZoneInfo("America/Chicago")
    except Exception:
        return timezone(timedelta(hours=-5), name="CT")


TZ_CST = _central_tz()


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None

    @property
    def spot(self) -> float | None:
        if (
            isinstance(self.bid, (int, float))
            and isinstance(self.ask, (int, float))
            and self.bid > 0
            and self.ask > 0
        ):
            return (float(self.bid) + float(self.ask)) / 2.0
        if isinstance(self.last, (int, float)) and self.last > 0:
            return float(self.last)
        return None


@dataclass(frozen=True)
class ProbeSnapshot:
    quote: QuoteSnapshot
    option_root: str
    expiry_iso: str
    greeks_by_symbol: dict[str, object]
    iv_atm: float | None
    iv_disp: float | None
    condor: Condor | None
    warnings: list[str]

    @property
    def signature(self) -> str:
        if self.condor is None:
            return f"{self.option_root}:{self.expiry_iso}:no-condor"
        return ":".join(
            [
                self.option_root,
                self.expiry_iso,
                f"{self.condor.short_put.strike:.0f}",
                f"{self.condor.long_put.strike:.0f}",
                f"{self.condor.short_call.strike:.0f}",
                f"{self.condor.long_call.strike:.0f}",
            ]
        )


def _as_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        value = float(value)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return value


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _build_publisher(engine_settings: Any):
    publisher_list = []
    if engine_settings.comms.engine_ingest_url:
        publisher_list.append(
            HttpPublisher(
                url=engine_settings.comms.engine_ingest_url,
                secret=engine_settings.comms.engine_ingest_secret or "",
            )
        )
    if engine_settings.comms.engine_facts_jsonl:
        publisher_list.append(
            JsonlPublisher(path=engine_settings.comms.engine_facts_jsonl)
        )
    return FanoutPublisher(publishers=publisher_list) if publisher_list else None


def build_probe_publisher(
    *,
    engine_settings: Any,
    direct_slack: bool,
    publish_web: bool,
    write_jsonl: bool,
    slack_webhook_url: str | None = None,
):
    publisher_list = []
    if publish_web and engine_settings.comms.engine_ingest_url:
        publisher_list.append(
            HttpPublisher(
                url=engine_settings.comms.engine_ingest_url,
                secret=engine_settings.comms.engine_ingest_secret or "",
            )
        )
    if write_jsonl and engine_settings.comms.engine_facts_jsonl:
        publisher_list.append(
            JsonlPublisher(path=engine_settings.comms.engine_facts_jsonl)
        )
    if direct_slack:
        publisher_list.append(SlackFactPublisher(webhook_url=slack_webhook_url))
    return FanoutPublisher(publishers=publisher_list) if publisher_list else None


def _run_id(engine_settings: Any) -> str:
    raw = engine_settings.comms.engine_run_id.strip()
    if raw:
        return raw
    return datetime.now(TZ_CST).strftime("%Y-%m-%d")


def _quote_data(payload: dict[str, Any]) -> dict[str, Any]:
    root = payload.get("QuoteResponse", {}) if isinstance(payload, dict) else {}
    items = _ensure_list(root.get("QuoteData")) if isinstance(root, dict) else []
    if items and isinstance(items[0], dict):
        return items[0]
    return {}


def _extract_quote(payload: dict[str, Any], fallback_symbol: str) -> QuoteSnapshot:
    item = _quote_data(payload)
    all_data = item.get("All", {}) if isinstance(item.get("All"), dict) else {}
    product = item.get("Product", {}) if isinstance(item.get("Product"), dict) else {}
    bid = _as_float(all_data.get("bid"))
    ask = _as_float(all_data.get("ask"))
    last = _as_float(all_data.get("lastTrade"))
    symbol = str(product.get("symbol") or fallback_symbol)
    return QuoteSnapshot(symbol=symbol, bid=bid, ask=ask, last=last)


def _iter_expiries(payload: dict[str, Any]) -> list[tuple[int, int, int]]:
    root = (
        payload.get("OptionExpireDateResponse", {}) if isinstance(payload, dict) else {}
    )
    items = []
    if isinstance(root, dict):
        items = _ensure_list(root.get("ExpirationDate") or root.get("expirationDates"))
    out: list[tuple[int, int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        year = item.get("year")
        month = item.get("month")
        day = item.get("day")
        if year is None or month is None or day is None:
            continue
        try:
            out.append((int(year), int(month), int(day)))
        except Exception:
            continue
    return sorted(set(out))


def _occ_symbol(
    root_symbol: str,
    year: int,
    month: int,
    day: int,
    right: str,
    strike: float,
) -> str:
    strike_int = int(round(float(strike) * 1000))
    return f"{root_symbol}{year % 100:02d}{month:02d}{day:02d}{right}{strike_int:08d}"


def _iter_chain_options(chain_payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = (
        chain_payload.get("OptionChainResponse", {})
        if isinstance(chain_payload, dict)
        else {}
    )
    pairs = []
    if isinstance(root, dict):
        pairs = _ensure_list(root.get("OptionPair") or root.get("optionPairs"))
    options: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        for key in ("Call", "Put", "call", "put", "optioncall", "optionPut"):
            option = pair.get(key)
            if isinstance(option, dict):
                options.append(option)
    return options


def _chain_greeks_map(
    *,
    option_root: str,
    expiry_year: int,
    expiry_month: int,
    expiry_day: int,
    chain_payload: dict[str, Any],
) -> dict[str, object]:
    greeks_by_symbol: dict[str, object] = {}
    for option in _iter_chain_options(chain_payload):
        option_greek = (
            option.get("OptionGreeks", {})
            if isinstance(option.get("OptionGreeks"), dict)
            else option.get("optionGreek", {})
            if isinstance(option.get("optionGreek"), dict)
            else {}
        )
        delta = _as_float(option_greek.get("delta"))
        if delta is None:
            continue
        iv = _as_float(option_greek.get("iv"))
        right_raw = str(option.get("callPut") or option.get("optionType") or "").upper()
        right = "C" if right_raw.startswith("C") else "P" if right_raw.startswith("P") else ""
        strike = _as_float(option.get("strikePrice"))
        if not right or strike is None:
            continue
        event_symbol = _occ_symbol(
            option_root,
            expiry_year,
            expiry_month,
            expiry_day,
            right,
            strike,
        )
        greeks_by_symbol[event_symbol] = SimpleNamespace(
            event_symbol=event_symbol,
            delta=delta,
            volatility=iv,
            bid_price=_as_float(option.get("bid")),
            ask_price=_as_float(option.get("ask")),
            last_price=_as_float(option.get("lastPrice")),
        )
    return greeks_by_symbol


def _roots(raw: str) -> list[str]:
    items = [part.strip().upper() for part in raw.split(",")]
    return [item for item in items if item]


def _snapshot(client: ETradeProbeClient, roots: list[str]) -> ProbeSnapshot:
    quote_payload = client.get_quote(roots[0], detail_flag="ALL")
    quote = _extract_quote(quote_payload, roots[0])
    warnings: list[str] = []
    errors: list[str] = []

    for option_root in roots:
        try:
            expiries = _iter_expiries(
                client.get_option_expire_dates(option_root, expiry_type="ALL")
            )
            if not expiries:
                warnings.append(f"{option_root}: no expiries returned")
                continue
            expiry_year, expiry_month, expiry_day = expiries[0]
            chain_payload = client.get_option_chain(
                option_root,
                expiry_year=expiry_year,
                expiry_month=expiry_month,
                expiry_day=expiry_day,
                price_type="ALL",
                strike_price_near=quote.spot,
                no_of_strikes=60,
                include_weekly=True,
            )
            greeks_by_symbol = _chain_greeks_map(
                option_root=option_root,
                expiry_year=expiry_year,
                expiry_month=expiry_month,
                expiry_day=expiry_day,
                chain_payload=chain_payload,
            )
            if not greeks_by_symbol:
                warnings.append(f"{option_root}: chain returned no usable delta data")
                continue

            spot = quote.spot
            if spot is None:
                raise RuntimeError("Quote response did not contain a usable spot price")
            iv_atm, iv_disp, n_calls, n_puts, parse_fail = compute_iv_0dte_band(
                greeks_by_symbol=greeks_by_symbol,
                spot=spot,
            )
            condor = pick_condor_20delta_20wide(greeks_by_symbol)
            if parse_fail:
                warnings.append(f"{option_root}: parse_fail={parse_fail}")
            if condor is None:
                warnings.append(f"{option_root}: condor build returned no match")
            return ProbeSnapshot(
                quote=quote,
                option_root=option_root,
                expiry_iso=f"{expiry_year:04d}-{expiry_month:02d}-{expiry_day:02d}",
                greeks_by_symbol=greeks_by_symbol,
                iv_atm=iv_atm,
                iv_disp=iv_disp,
                condor=condor,
                warnings=warnings + [f"chain_counts calls={n_calls} puts={n_puts}"],
            )
        except Exception as exc:
            errors.append(f"{option_root}: {exc}")

    error_blob = "; ".join(errors or warnings) or "unknown E*TRADE chain failure"
    raise RuntimeError(error_blob)


def build_probe_fact(
    *,
    snapshot: ProbeSnapshot,
    underlier: str,
    run_id: str,
    seq: int,
) -> EngineFactV1:
    quote = snapshot.quote
    ts_cst = datetime.now(TZ_CST).strftime("%Y-%m-%d %H:%M CST")
    metrics: dict[str, float] = {}
    if quote.spot is not None:
        metrics["close"] = quote.spot
        metrics["latest_spx_mid"] = quote.spot
    if snapshot.iv_atm is not None:
        metrics["iv_atm"] = snapshot.iv_atm
    if snapshot.iv_disp is not None:
        metrics["iv_disp"] = snapshot.iv_disp
    signal: dict[str, Any] = {
        "touch": False,
        "probe_root": snapshot.option_root,
        "probe_expiry": snapshot.expiry_iso,
        "greeks_count": len(snapshot.greeks_by_symbol),
        "quote_bid": quote.bid,
        "quote_ask": quote.ask,
        "quote_last": quote.last,
        "warnings": snapshot.warnings,
    }
    if snapshot.condor is not None:
        signal.update(
            {
                "short_put": snapshot.condor.short_put.strike,
                "long_put": snapshot.condor.long_put.strike,
                "short_call": snapshot.condor.short_call.strike,
                "long_call": snapshot.condor.long_call.strike,
                "net_short_delta": snapshot.condor.net_short_delta,
            }
        )
    return EngineFactV1(
        kind=FactKind.STRATEGY_SIGNAL,
        run_id=run_id,
        seq=seq,
        ts=datetime.now(timezone.utc).isoformat(),
        market=MarketRef(underlier=underlier, session="RTH", timeframe="snapshot"),
        metrics=metrics,
        state={
            "ts_cst": ts_cst,
            "signal_mode": "etrade_probe",
            "data_source": "etrade_probe",
            "probe_root": snapshot.option_root,
            "probe_expiry": snapshot.expiry_iso,
            "gate_ok": snapshot.condor is not None,
            "greeks_count": len(snapshot.greeks_by_symbol),
        },
        strategy="etrade_condor_probe",
        signal=signal,
    )


class ETradeProbeRunner:
    def __init__(self, *, engine_settings: Any, runtime: Any, log: Any):
        self.engine_settings = engine_settings
        self.runtime = runtime
        self.log = log
        self.client = ETradeProbeClient(load_probe_config(engine_settings.auth))
        self.publisher = _build_publisher(engine_settings)
        self.run_id = _run_id(engine_settings)
        self.seq = 0
        self.last_signature: str | None = None
        self.last_sent_monotonic: float = 0.0
        runner_cfg = engine_settings.runner
        self.poll_seconds = max(
            15, int(getattr(runner_cfg, "etrade_poll_seconds", 60))
        )
        self.cooldown_seconds = max(
            30, int(getattr(runner_cfg, "etrade_signal_cooldown_seconds", 300))
        )
        self.run_once = bool(getattr(runner_cfg, "etrade_run_once", False))
        self.option_roots = _roots(
            getattr(runner_cfg, "etrade_option_roots", "SPX,SPXW,XSP")
        )

    def _publish(self, fact: EngineFactV1) -> None:
        if self.publisher is None:
            return
        self.publisher.publish(fact)

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _fact(
        self,
        *,
        kind: FactKind,
        metrics: dict[str, float] | None = None,
        state: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        strategy: str | None = None,
        signal: dict[str, Any] | None = None,
    ) -> EngineFactV1:
        return EngineFactV1(
            kind=kind,
            run_id=self.run_id,
            seq=self._next_seq(),
            ts=datetime.now(timezone.utc).isoformat(),
            market=MarketRef(
                underlier=self.option_roots[0],
                session="RTH",
                timeframe="snapshot",
            ),
            metrics=metrics or {},
            state=state or {},
            events=events or [],
            strategy=strategy,
            signal=signal,
        )

    def publish_text_event(
        self,
        name: str,
        text: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._publish(
            self._fact(
                kind=FactKind.ENGINE_EVENT,
                state={"data_source": "etrade_probe"},
                events=[{"name": name, "text": text, "payload": payload or {}}],
            )
        )

    def publish_snapshot(self, snapshot: ProbeSnapshot) -> None:
        fact = build_probe_fact(
            snapshot=snapshot,
            underlier=self.option_roots[0],
            run_id=self.run_id,
            seq=self._next_seq(),
        )
        self._publish(fact)

    def should_emit(self, signature: str) -> bool:
        now = time.monotonic()
        if self.last_signature != signature:
            return True
        return (now - self.last_sent_monotonic) >= self.cooldown_seconds

    def run(self) -> None:
        self.publish_text_event(
            "startup",
            (
                f":rocket: E*TRADE probe starting (roots={','.join(self.option_roots)}, "
                f"poll={self.poll_seconds}s, cooldown={self.cooldown_seconds}s)."
            ),
        )
        while True:
            try:
                snapshot = _snapshot(self.client, self.option_roots)
                if self.should_emit(snapshot.signature):
                    self.publish_snapshot(snapshot)
                    self.last_signature = snapshot.signature
                    self.last_sent_monotonic = time.monotonic()
                    self.log.info(
                        "[ETRADE_PROBE] emitted root=%s expiry=%s greeks=%d condor=%s",
                        snapshot.option_root,
                        snapshot.expiry_iso,
                        len(snapshot.greeks_by_symbol),
                        ("yes" if snapshot.condor is not None else "no"),
                    )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log.exception("[ETRADE_PROBE] cycle failed")
                self.publish_text_event(
                    "etrade_probe_error",
                    f":warning: E*TRADE probe cycle failed: `{exc}`",
                    payload={"error": str(exc)},
                )
            if self.run_once:
                break
            time.sleep(self.poll_seconds)
        self.publish_text_event("shutdown", ":stop_sign: E*TRADE probe stopped.")


def run_etrade_probe(*, engine_settings: Any, runtime: Any, log: Any) -> None:
    runner = ETradeProbeRunner(engine_settings=engine_settings, runtime=runtime, log=log)
    runner.run()
