from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.apps.captain_log.settings import CaptainLogSettings
from starship_engine.apps.comms.settings import CommsSettings
from starship_engine.apps.context_overnight.settings import ContextOvernightSettings
from starship_engine.apps.exit_planner.settings import ExitPlannerSettings
from starship_engine.apps.heston.settings import HestonSettings
from starship_engine.apps.sentinel.settings import SentinelSettings
from starship_engine.apps.stream.settings import StreamSettings
from starship_engine.apps.trend_risk.settings import TrendRiskSettings


class PersistCandleSettings(BaseModel):
    enabled: bool = False
    out_dir: str = "data/candles"
    file_prefix: str = "SPX_5m"


class PersistOptionsSettings(BaseModel):
    enabled: bool = False
    out_dir: str = "data/options"
    file_prefix: str = "SPX_0DTE"
    dte: int = 0


class MarketPersistSettings(BaseModel):
    candles: PersistCandleSettings = PersistCandleSettings()
    options: PersistOptionsSettings = PersistOptionsSettings()


class RunnerSettings(BaseModel):
    dry_run: bool = False
    ignore_time_window: bool = False
    signal_mode: str = "spx"
    exec_symbol: str = "SPX"
    context_symbol: str = "/ES"
    context_exchange: str = "CME"
    context_code: str = "ES"
    warmup_bars: int = 120
    backfill_bars: int = 600
    candle_minutes_back_override: int = 900
    backfill_prev_session_open: bool = True
    idle_warn_seconds: int = 60
    idle_slack_cooldown: int = 600
    early_alerts_enabled: bool = False
    early_min_bars: int = 18
    early_require_calm: bool = True
    early_require_rv30_le_rv60: bool = True
    early_vrp_min: float = 1.60
    early_mr_max: float = 0.03
    early_rv60_min: float = 0.06
    early_vov_max: float | None = None
    policy_path: str = "configs/policies/active.yaml"
    require_ema_touch: bool = True
    policy_name: str = "ic_harvest_v1"
    policy_max_trades_per_day: int = 3
    policy_entry_cutoff_trades: int = 2
    policy_per_trade_stop_r: float | None = None
    policy_daily_stop_r: float = 1.5
    policy_no_new_entries_after_cst: str = "13:45"
    policy_exit_all_by_cst: str = "14:45"
    policy_tp_targets: str = "0.45,0.25,0.25"
    log_level: str = "INFO"
    log_file: str = "logs/starship_engine.log"
    log_daily: bool = False
    log_daily_backup_count: int = 14


class EngineSettings(BaseModel):
    installed_apps: list[str] = []
    trend_risk: TrendRiskSettings = TrendRiskSettings()
    sentinel: SentinelSettings = SentinelSettings()
    captain_log: CaptainLogSettings = CaptainLogSettings()
    comms: CommsSettings = CommsSettings()
    exit_planner: ExitPlannerSettings = ExitPlannerSettings()
    heston: HestonSettings = HestonSettings()
    context_overnight: ContextOvernightSettings = ContextOvernightSettings()
    auth: AuthSettings = AuthSettings()
    stream: StreamSettings = StreamSettings()
    runner: RunnerSettings = RunnerSettings()
    market_persist: MarketPersistSettings = MarketPersistSettings()


def load_engine_settings(path: Path) -> EngineSettings:
    if not path.exists():
        return EngineSettings()
    try:
        import yaml
    except Exception:
        return EngineSettings()
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        return EngineSettings()
    return EngineSettings.model_validate(data)


def apply_env_overrides(
    settings: EngineSettings, env: dict[str, str]
) -> EngineSettings:
    au = settings.auth.model_dump()
    auth_map: dict[str, tuple[str, type]] = {
        "TT_CLIENT_ID": ("client_id", str),
        "TT_CLIENT_SECRET": ("client_secret", str),
        "TT_REFRESH_TOKEN": ("refresh_token", str),
        "TT_IS_TEST": ("is_test", bool),
    }
    for env_key, (field, cast) in auth_map.items():
        raw = env.get(env_key)
        if raw is None or raw.strip() == "":
            continue
        if cast is bool:
            au[field] = raw.strip().lower() in ("1", "true", "yes", "y", "on")
        else:
            au[field] = raw.strip()
    return EngineSettings(
        installed_apps=settings.installed_apps,
        trend_risk=settings.trend_risk,
        sentinel=settings.sentinel,
        captain_log=settings.captain_log,
        comms=settings.comms,
        exit_planner=settings.exit_planner,
        heston=settings.heston,
        context_overnight=settings.context_overnight,
        auth=AuthSettings.model_validate(au),
        stream=settings.stream,
        runner=settings.runner,
        market_persist=settings.market_persist,
    )
