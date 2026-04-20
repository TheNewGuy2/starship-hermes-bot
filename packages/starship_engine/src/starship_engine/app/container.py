from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from starship_shared.policy import TradePolicy

from starship_engine.apps.auth.logic import require_tastytrade_session
from starship_engine.apps.captain_log.app import build_state_log_handler
from starship_engine.apps.comms.publishers import (
    FanoutPublisher,
    HttpPublisher,
    JsonlPublisher,
)
from starship_engine.apps.exit_planner.app import to_config as exit_planner_to_config
from starship_engine.apps.heston.app import to_params as heston_to_params
from starship_engine.apps.sentinel.core import SentinelParams, SlackSentinel
from starship_engine.apps.sentinel.state import StateParams
from starship_engine.apps.stream.futures import resolve_future_streamer_symbol
from starship_engine.apps.trend_risk.app import to_config as trend_risk_to_config
from starship_engine.domain.handlers import (
    EsContextHandler,
    ExitPlannerHandler,
    StatsHandler,
    StreamHealthHandler,
)
from starship_engine.domain.types import EarlyConfig

TZ_CST = ZoneInfo("America/Chicago")


@dataclass
class RuntimeSettings:
    engine_cfg_path: Path
    dry_run: bool
    ignore_time_window: bool
    signal_mode: str
    exec_symbol: str
    context_symbol: str
    context_exchange: str
    context_code: str
    warmup_bars: int
    backfill_bars: int
    candle_minutes_back_override: int
    backfill_prev_session_open: bool
    idle_warn_seconds: int
    idle_slack_cooldown: int
    early_alerts_enabled: bool
    early_min_bars: int
    early_require_calm: bool
    early_require_rv30_le_rv60: bool
    early_vrp_min: float
    early_mr_max: float
    early_rv60_min: float
    early_vov_max: Optional[float]
    policy_path: Path
    require_ema_touch: bool
    policy_name: str
    policy_max_trades_per_day: int
    policy_entry_cutoff_trades: int
    policy_per_trade_stop_r: Optional[float]
    policy_daily_stop_r: float
    policy_no_new_entries_after_cst: str
    policy_exit_all_by_cst: str
    policy_tp_targets: str


@dataclass
class EngineDeps:
    engine_settings: Any
    runtime: RuntimeSettings
    log: Any = None
    session: Any = None
    publisher: Any = None
    trade_policy: Any = None
    sentinel: Any = None
    gate_params: Any = None
    exit_planner_cfg: Any = None
    trend_risk_cfg: Any = None
    early_config: Any = None
    state_log_handler: Any = None
    stream_health: Any = None
    stats: Any = None
    exit_planner: Any = None
    context_handler: Any = None
    context_enabled: bool = False
    engine_run_id: str = ""


def _build_trade_policy_from_runtime(runtime: RuntimeSettings) -> TradePolicy:
    tp_list: list[float] = []
    for part in runtime.policy_tp_targets.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            tp_list.append(float(part))
        except Exception:
            continue
    return TradePolicy(
        name=runtime.policy_name,
        tp_targets=tp_list or [0.45, 0.25, 0.25],
        max_trades_per_day=runtime.policy_max_trades_per_day,
        entry_cutoff_trades=runtime.policy_entry_cutoff_trades,
        per_trade_stop_r=runtime.policy_per_trade_stop_r,
        daily_stop_r=runtime.policy_daily_stop_r,
        no_new_entries_after_cst=runtime.policy_no_new_entries_after_cst,
        exit_all_by_cst=runtime.policy_exit_all_by_cst,
    )


def _load_trade_policy_from_file(path: Path) -> TradePolicy | None:
    if not path.exists():
        return None
    try:
        import yaml
    except Exception:
        return None
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        return None
    return TradePolicy(**data)


def build_container(
    engine_settings: Any, runtime: RuntimeSettings, log: Any
) -> EngineDeps:
    state_log_handler = build_state_log_handler(engine_settings.captain_log)
    session = require_tastytrade_session(engine_settings.auth)

    context_enabled = (
        runtime.signal_mode == "es_context" or engine_settings.context_overnight.enabled
    )
    if context_enabled and runtime.context_symbol in ("ES", "/ES", "AUTO", "auto"):
        resolved = resolve_future_streamer_symbol(
            session,
            exchange=runtime.context_exchange,
            code=runtime.context_code,
        )
        if resolved:
            log.info(
                "[CONTEXT] resolved %s/%s -> %s",
                runtime.context_exchange,
                runtime.context_code,
                resolved,
            )
            runtime.context_symbol = resolved
        else:
            log.warning(
                "[CONTEXT] failed to resolve %s/%s; using %s",
                runtime.context_exchange,
                runtime.context_code,
                runtime.context_symbol,
            )

    engine_ingest_url = engine_settings.comms.engine_ingest_url
    engine_ingest_secret = engine_settings.comms.engine_ingest_secret
    engine_facts_jsonl = engine_settings.comms.engine_facts_jsonl
    publisher_list = []
    if engine_ingest_url and engine_ingest_secret:
        publisher_list.append(
            HttpPublisher(url=engine_ingest_url, secret=engine_ingest_secret)
        )
    if engine_facts_jsonl:
        publisher_list.append(JsonlPublisher(path=engine_facts_jsonl))
    publisher = FanoutPublisher(publishers=publisher_list) if publisher_list else None

    trade_policy = _load_trade_policy_from_file(
        runtime.policy_path
    ) or _build_trade_policy_from_runtime(runtime)

    gate_params = heston_to_params(engine_settings.heston)
    exit_planner_cfg = exit_planner_to_config(engine_settings.exit_planner)
    trend_risk_cfg = trend_risk_to_config(engine_settings.trend_risk)
    log.info(
        "[HESTON] Gate enabled=%s vrp_min=%.2f mr_max=%.4f vov_max=%s",
        gate_params.enabled,
        gate_params.vrp_min,
        gate_params.mr_max,
        gate_params.vov_max,
    )
    early_config = EarlyConfig(
        enabled=runtime.early_alerts_enabled,
        min_bars=runtime.early_min_bars,
        require_calm=runtime.early_require_calm,
        require_rv30_le_rv60=runtime.early_require_rv30_le_rv60,
        vrp_min=runtime.early_vrp_min,
        mr_max=runtime.early_mr_max,
        rv60_min=runtime.early_rv60_min,
        vov_max=runtime.early_vov_max,
    )

    sentinel_enabled = engine_settings.sentinel.enabled
    sentinel_rth_only = engine_settings.sentinel.rth_only
    sentinel_tz_name = engine_settings.sentinel.timezone
    try:
        sentinel_tz = ZoneInfo(sentinel_tz_name)
    except Exception:
        sentinel_tz = TZ_CST
        log.warning(
            "[SENTINEL] Invalid timezone %s; using %s", sentinel_tz_name, TZ_CST
        )

    score_thresholds = engine_settings.sentinel.score_thresholds
    score_hysteresis = engine_settings.sentinel.score_hysteresis
    vrp_min = engine_settings.sentinel.vrp_min
    mr_max = engine_settings.sentinel.mr_max
    vov_caution = engine_settings.sentinel.vov_caution
    vov_hostile = engine_settings.sentinel.vov_hostile
    vov_hysteresis = engine_settings.sentinel.vov_hysteresis

    sentinel_params = SentinelParams(
        score_thresholds=score_thresholds,
        hysteresis=score_hysteresis,
        vrp_min=vrp_min,
        mr_max=mr_max,
        vov_caution=vov_caution,
        vov_hostile=vov_hostile,
        vov_hysteresis=vov_hysteresis,
        cooldown_regime_change_seconds=engine_settings.sentinel.cooldown_regime_change_seconds,
        cooldown_score_cross_seconds=engine_settings.sentinel.cooldown_score_cross_seconds,
        cooldown_vrp_seconds=engine_settings.sentinel.cooldown_vrp_seconds,
        cooldown_vov_seconds=engine_settings.sentinel.cooldown_vov_seconds,
        cooldown_mr_seconds=engine_settings.sentinel.cooldown_mr_seconds,
    )
    sentinel_state_params = StateParams(
        vrp_friendly_min=vrp_min,
        vrp_caution_min=engine_settings.sentinel.vrp_caution_min,
        mr_max=mr_max,
        vov_caution=vov_caution,
        vov_hostile=vov_hostile,
    )
    sentinel_heartbeat_enabled = engine_settings.sentinel.heartbeat_enabled
    sentinel_heartbeat_minutes = engine_settings.sentinel.heartbeat_minutes
    sentinel_heartbeat_rth_only = engine_settings.sentinel.heartbeat_rth_only

    sentinel = SlackSentinel(
        params=sentinel_params,
        state_params=sentinel_state_params,
        tz=sentinel_tz,
        enabled=sentinel_enabled,
        rth_only=sentinel_rth_only,
        heartbeat_enabled=sentinel_heartbeat_enabled,
        heartbeat_minutes=sentinel_heartbeat_minutes,
        heartbeat_rth_only=sentinel_heartbeat_rth_only,
    )

    stats = StatsHandler()
    context_handler = EsContextHandler(enabled=context_enabled)
    stream_health = StreamHealthHandler(
        idle_warn_seconds=runtime.idle_warn_seconds,
        idle_slack_cooldown=runtime.idle_slack_cooldown,
        on_idle=None,
    )
    exit_planner = None
    if exit_planner_cfg.enabled:
        exit_planner = ExitPlannerHandler(
            cfg=exit_planner_cfg,
            vov_series=deque(maxlen=exit_planner_cfg.vov_window),
        )

    engine_run_id = engine_settings.comms.engine_run_id.strip()
    if not engine_run_id:
        engine_run_id = datetime.now(TZ_CST).strftime("%Y-%m-%d")

    return EngineDeps(
        engine_settings=engine_settings,
        runtime=runtime,
        log=log,
        session=session,
        publisher=publisher,
        trade_policy=trade_policy,
        sentinel=sentinel,
        gate_params=gate_params,
        exit_planner_cfg=exit_planner_cfg,
        trend_risk_cfg=trend_risk_cfg,
        early_config=early_config,
        state_log_handler=state_log_handler,
        stream_health=stream_health,
        stats=stats,
        exit_planner=exit_planner,
        context_handler=context_handler,
        context_enabled=context_enabled,
        engine_run_id=engine_run_id,
    )
