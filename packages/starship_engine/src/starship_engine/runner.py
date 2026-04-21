# src/bot/runner.py
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.apps.captain_log.logging import get_logger, setup_logging
from starship_engine.apps.etrade_probe.app import run_etrade_probe
from starship_engine.brokers.base import BrokerProvider
from starship_engine.config import load_runtime_env
from starship_engine.core.apps import load_apps
from starship_engine.core.settings import apply_env_overrides, load_engine_settings


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name, default)
    return v.strip() if isinstance(v, str) else str(v)


def _hash_secret(value: str) -> str:
    if not value:
        return "empty"
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _broker_provider(settings: AuthSettings) -> BrokerProvider:
    raw = (settings.provider or "").strip().lower() or BrokerProvider.TASTYTRADE.value
    try:
        return BrokerProvider(raw)
    except ValueError as exc:
        raise RuntimeError(f"Unsupported broker provider: {settings.provider!r}") from exc


def main() -> None:
    dotenv_path, secrets_dir = load_runtime_env()

    # -----------------------------
    # ENV
    # -----------------------------
    engine_cfg_path = Path(_env_str("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    engine_settings = load_engine_settings(engine_cfg_path)
    engine_settings = apply_env_overrides(engine_settings, dict(os.environ))
    setup_logging(
        level_name=engine_settings.runner.log_level,
        log_file=engine_settings.runner.log_file,
        log_daily=engine_settings.runner.log_daily,
        log_daily_backup_count=engine_settings.runner.log_daily_backup_count,
    )
    log = get_logger("starship_engine.runner")
    log.info(
        "[CONFIG] engine_cfg_path=%s comms_url=%s comms_secret=%s facts_jsonl=%s",
        engine_cfg_path,
        engine_settings.comms.engine_ingest_url,
        _hash_secret(engine_settings.comms.engine_ingest_secret),
        engine_settings.comms.engine_facts_jsonl,
    )
    log.info(
        "[CONFIG] runtime_env dotenv=%s secrets_dir=%s",
        (dotenv_path if dotenv_path else "not_found"),
        (secrets_dir if secrets_dir else "not_set"),
    )
    provider = _broker_provider(engine_settings.auth)
    log.info("[AUTH] broker_provider=%s", provider.value)
    installed_apps = engine_settings.installed_apps
    if installed_apps:
        try:
            _apps = load_apps(installed_apps)
            names = []
            for app in _apps:
                if hasattr(app, "name"):
                    names.append(str(getattr(app, "name")))
                else:
                    names.append(str(getattr(app, "__name__", app)))
            log.info("Loaded apps: %s", ", ".join(names))
        except Exception:
            log.exception("Failed to load installed apps")

    runner_cfg = engine_settings.runner

    dry_run = runner_cfg.dry_run
    ignore_time_window = runner_cfg.ignore_time_window

    signal_mode = runner_cfg.signal_mode.lower()
    if signal_mode not in ("spx", "es_context"):
        log.warning("Unknown SIGNAL_MODE=%s; defaulting to spx", signal_mode)
        signal_mode = "spx"

    exec_symbol = runner_cfg.exec_symbol or "SPX"
    context_symbol = runner_cfg.context_symbol or "/ES"
    context_exchange = runner_cfg.context_exchange
    context_code = runner_cfg.context_code
    warmup_bars = runner_cfg.warmup_bars
    backfill_bars = runner_cfg.backfill_bars
    candle_minutes_back_override = runner_cfg.candle_minutes_back_override
    backfill_prev_session_open = runner_cfg.backfill_prev_session_open

    # stream watchdog params (runner-side)
    idle_warn_seconds = runner_cfg.idle_warn_seconds
    idle_slack_cooldown = runner_cfg.idle_slack_cooldown  # 10 min

    # Engine no longer sends Slack; web owns formatting + delivery.

    early_alerts_enabled = runner_cfg.early_alerts_enabled
    early_min_bars = runner_cfg.early_min_bars
    early_require_calm = runner_cfg.early_require_calm
    early_require_rv30_le_rv60 = runner_cfg.early_require_rv30_le_rv60
    early_vrp_min = runner_cfg.early_vrp_min
    early_mr_max = runner_cfg.early_mr_max
    early_rv60_min = runner_cfg.early_rv60_min
    early_vov_max = runner_cfg.early_vov_max
    engine_ingest_url = engine_settings.comms.engine_ingest_url
    engine_ingest_secret = engine_settings.comms.engine_ingest_secret
    engine_facts_jsonl = engine_settings.comms.engine_facts_jsonl
    policy_path = Path(runner_cfg.policy_path)
    # EMA touch gating can be disabled early in the session before the EMA is warmed up.
    # Set REQUIRE_EMA_TOUCH=false to ignore the EMA/touch filter and rely on other signals.
    require_ema_touch = runner_cfg.require_ema_touch
    runtime_kwargs = dict(
        engine_cfg_path=engine_cfg_path,
        dry_run=dry_run,
        ignore_time_window=ignore_time_window,
        signal_mode=signal_mode,
        exec_symbol=exec_symbol,
        context_symbol=context_symbol,
        context_exchange=context_exchange,
        context_code=context_code,
        warmup_bars=warmup_bars,
        backfill_bars=backfill_bars,
        candle_minutes_back_override=candle_minutes_back_override,
        backfill_prev_session_open=backfill_prev_session_open,
        idle_warn_seconds=idle_warn_seconds,
        idle_slack_cooldown=idle_slack_cooldown,
        early_alerts_enabled=early_alerts_enabled,
        early_min_bars=early_min_bars,
        early_require_calm=early_require_calm,
        early_require_rv30_le_rv60=early_require_rv30_le_rv60,
        early_vrp_min=early_vrp_min,
        early_mr_max=early_mr_max,
        early_rv60_min=early_rv60_min,
        early_vov_max=early_vov_max,
        policy_path=policy_path,
        require_ema_touch=require_ema_touch,
        policy_name=runner_cfg.policy_name,
        policy_max_trades_per_day=runner_cfg.policy_max_trades_per_day,
        policy_entry_cutoff_trades=runner_cfg.policy_entry_cutoff_trades,
        policy_per_trade_stop_r=runner_cfg.policy_per_trade_stop_r,
        policy_daily_stop_r=runner_cfg.policy_daily_stop_r,
        policy_no_new_entries_after_cst=runner_cfg.policy_no_new_entries_after_cst,
        policy_exit_all_by_cst=runner_cfg.policy_exit_all_by_cst,
        policy_tp_targets=runner_cfg.policy_tp_targets,
    )
    if provider is BrokerProvider.ETRADE:
        log.info("Runner starting in E*TRADE probe mode")
        runtime = SimpleNamespace(**runtime_kwargs)
        run_etrade_probe(engine_settings=engine_settings, runtime=runtime, log=log)
        return
    from starship_engine.app.container import RuntimeSettings, build_container
    from starship_engine.app.engine import Engine

    runtime = RuntimeSettings(**runtime_kwargs)
    deps = build_container(engine_settings, runtime, log)
    log.info("Runner starting")
    log.info(
        "ENV dry_run=%s ignore_time_window=%s warmup_bars=%d",
        dry_run,
        ignore_time_window,
        warmup_bars,
    )
    log.info(
        "SIGNAL_MODE=%s exec_symbol=%s context_symbol=%s",
        signal_mode,
        exec_symbol,
        (runtime.context_symbol if deps.context_enabled else "-"),
    )
    log.info(
        "PUBLISH url=%s jsonl=%s",
        (engine_ingest_url or "disabled"),
        engine_facts_jsonl or "disabled",
    )
    log.info(
        "[STATELOG] enabled=%s fmt=%s dir=%s every_bars=%d",
        engine_settings.captain_log.enabled,
        engine_settings.captain_log.fmt,
        engine_settings.captain_log.out_dir,
        engine_settings.captain_log.every_bars,
    )
    log.info("[AUTH] Session created / authenticated.")

    engine = Engine(deps)
    engine.run()


if __name__ == "__main__":
    main()
