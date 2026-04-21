# src/bot/cli.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import typer

from starship_engine.runner import main as run_main

app = typer.Typer(add_completion=False)
policy_app = typer.Typer(help="Policy tools")
app.add_typer(policy_app, name="policy")


def _temp_config_with_runner_overrides(**runner_overrides: object) -> tuple[Path, object]:
    import yaml

    from starship_engine.core.settings import RunnerSettings, load_engine_settings

    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    settings = load_engine_settings(cfg_path)
    runner = settings.runner.model_dump()
    for field, value in runner_overrides.items():
        if value is None:
            continue
        runner[field] = value
    settings.runner = RunnerSettings.model_validate(runner)
    with tempfile.NamedTemporaryFile(
        prefix="starship-config-", suffix=".yaml", delete=False
    ) as tmp:
        tmp.write(
            yaml.safe_dump(settings.model_dump(), sort_keys=False).encode("utf-8")
        )
        path = Path(tmp.name)
    return path, settings


@app.command("run")
def run(
    signal_mode: Optional[str] = typer.Option(
        None, help="Signal mode: spx or es_context"
    ),
    context_symbol: Optional[str] = typer.Option(None, help="Context symbol (ES)"),
    context_exchange: Optional[str] = typer.Option(None, help="Context exchange"),
    context_code: Optional[str] = typer.Option(None, help="Context code"),
    warmup_bars: Optional[int] = typer.Option(None, help="Warmup bars"),
    dry_run: Optional[bool] = typer.Option(None, help="Dry run"),
    ignore_time_window: Optional[bool] = typer.Option(None, help="Ignore time window"),
    require_ema_touch: Optional[bool] = typer.Option(None, help="Require EMA touch"),
    early_alerts_enabled: Optional[bool] = typer.Option(
        None, help="Early alerts enabled"
    ),
    early_min_bars: Optional[int] = typer.Option(None, help="Early min bars"),
    early_require_calm: Optional[bool] = typer.Option(None, help="Early require calm"),
    early_require_rv30_le_rv60: Optional[bool] = typer.Option(
        None, help="Early RV30 <= RV60"
    ),
    early_vrp_min: Optional[float] = typer.Option(None, help="Early VRP min"),
    early_mr_max: Optional[float] = typer.Option(None, help="Early MR max"),
    early_rv60_min: Optional[float] = typer.Option(None, help="Early RV60 min"),
    early_vov_max: Optional[float] = typer.Option(None, help="Early VoV max"),
) -> None:
    config_path, _settings = _temp_config_with_runner_overrides(
        signal_mode=signal_mode,
        context_symbol=context_symbol,
        context_exchange=context_exchange,
        context_code=context_code,
        warmup_bars=warmup_bars,
        dry_run=dry_run,
        ignore_time_window=ignore_time_window,
        require_ema_touch=require_ema_touch,
        early_alerts_enabled=early_alerts_enabled,
        early_min_bars=early_min_bars,
        early_require_calm=early_require_calm,
        early_require_rv30_le_rv60=early_require_rv30_le_rv60,
        early_vrp_min=early_vrp_min,
        early_mr_max=early_mr_max,
        early_rv60_min=early_rv60_min,
        early_vov_max=early_vov_max,
    )
    os.environ["ENGINE_CONFIG_PATH"] = str(config_path)
    run_main()


@app.command("probe-etrade")
def probe_etrade(
    once: bool = typer.Option(
        True, "--once/--loop", help="Run one E*TRADE probe cycle or keep polling"
    ),
    poll_seconds: int = typer.Option(60, help="Polling interval when running in loop mode"),
    cooldown_seconds: int = typer.Option(
        300, help="Minimum seconds between repeated identical alerts"
    ),
    option_roots: str = typer.Option(
        "SPX,SPXW,XSP", help="Comma-separated option roots to try in order"
    ),
    direct_slack: bool = typer.Option(
        True, "--direct-slack/--no-direct-slack", help="Send Slack directly from the command"
    ),
    publish_web: bool = typer.Option(
        False, "--publish-web/--no-publish-web", help="Also publish facts to the local web ingest endpoint"
    ),
    write_jsonl: bool = typer.Option(
        True, "--write-jsonl/--no-write-jsonl", help="Append emitted facts to the configured JSONL file"
    ),
) -> None:
    from starship_engine.apps.captain_log.logging import get_logger, setup_logging
    from starship_engine.apps.etrade_probe.app import (
        ETradeProbeRunner,
        build_probe_publisher,
    )
    from starship_engine.core.settings import apply_env_overrides, load_engine_settings

    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    config_path, settings = _temp_config_with_runner_overrides(
        etrade_run_once=once,
        etrade_poll_seconds=poll_seconds,
        etrade_signal_cooldown_seconds=cooldown_seconds,
        etrade_option_roots=option_roots,
    )
    os.environ["ENGINE_CONFIG_PATH"] = str(config_path)
    settings = apply_env_overrides(load_engine_settings(config_path), dict(os.environ))
    setup_logging(
        level_name=settings.runner.log_level,
        log_file=settings.runner.log_file,
        log_daily=settings.runner.log_daily,
        log_daily_backup_count=settings.runner.log_daily_backup_count,
    )
    log = get_logger("starship_engine.cli")
    runtime = {
        "mode": "etrade_probe",
        "once": once,
        "poll_seconds": poll_seconds,
        "cooldown_seconds": cooldown_seconds,
    }
    runner = ETradeProbeRunner(engine_settings=settings, runtime=runtime, log=log)
    runner.publisher = build_probe_publisher(
        engine_settings=settings,
        direct_slack=direct_slack,
        publish_web=publish_web,
        write_jsonl=write_jsonl,
    )
    runner.run()


@policy_app.command("promote")
def policy_promote(
    run_summary_json: str = typer.Argument(..., help="Path to summary.json"),
    name: str = typer.Option(..., help="Policy name"),
    out: Optional[str] = typer.Option(None, help="Output policy path"),
    notes: Optional[str] = typer.Option(None, help="Optional notes"),
    set_active: bool = typer.Option(False, "--set-active", help="Set as active policy"),
) -> None:
    import hashlib

    import yaml
    from starship_shared.policy import TradePolicy

    summary_path = Path(run_summary_json)
    summary = json.loads(summary_path.read_text())
    params = summary.get("params", {})
    tp_seq = params.get("tp_r_seq")
    if not tp_seq:
        tp_r = params.get("tp_r")
        if tp_r is None:
            raise ValueError("tp_r or tp_r_seq missing in summary.json")
        tp_seq = [tp_r]

    cfg = TradePolicy(
        name=name,
        tp_targets=params.get("tp_targets", [0.45, 0.25, 0.25]),
        max_trades_per_day=int(params.get("max_trades_per_day", 3)),
        entry_cutoff_trades=int(params.get("entry_cutoff_trades", 2)),
        per_trade_stop_r=params.get("stop_r"),
        daily_stop_r=params.get("daily_stop_r"),
        no_new_entries_after_cst=params.get("no_new_entries_after_cst", "13:45"),
        exit_all_by_cst=params.get("exit_all_by_cst", "14:45"),
        tp_r_seq=tp_seq,
        notes=notes,
    )

    payload = cfg.to_dict()
    payload["summary_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    out_path = Path(out) if out else Path("configs/policies") / f"{name}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    print(f"Wrote policy: {out_path}")

    if set_active:
        active_path = Path("configs/policies/active.yaml")
        active_path.write_text(out_path.read_text())
        print("Set as active policy")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
