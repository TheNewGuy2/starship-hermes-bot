# src/bot/cli.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from starship_engine.runner import main as run_main

app = typer.Typer(add_completion=False)
policy_app = typer.Typer(help="Policy tools")
app.add_typer(policy_app, name="policy")


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
    import os
    import tempfile

    import yaml

    from starship_engine.core.settings import RunnerSettings, load_engine_settings

    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    settings = load_engine_settings(cfg_path)
    runner = settings.runner.model_dump()

    def _set(field: str, value: object) -> None:
        if value is None:
            return
        runner[field] = value

    _set("signal_mode", signal_mode)
    _set("context_symbol", context_symbol)
    _set("context_exchange", context_exchange)
    _set("context_code", context_code)
    _set("warmup_bars", warmup_bars)
    _set("dry_run", dry_run)
    _set("ignore_time_window", ignore_time_window)
    _set("require_ema_touch", require_ema_touch)
    _set("early_alerts_enabled", early_alerts_enabled)
    _set("early_min_bars", early_min_bars)
    _set("early_require_calm", early_require_calm)
    _set("early_require_rv30_le_rv60", early_require_rv30_le_rv60)
    _set("early_vrp_min", early_vrp_min)
    _set("early_mr_max", early_mr_max)
    _set("early_rv60_min", early_rv60_min)
    _set("early_vov_max", early_vov_max)

    settings.runner = RunnerSettings.model_validate(runner)

    with tempfile.NamedTemporaryFile(
        prefix="starship-config-", suffix=".yaml", delete=False
    ) as tmp:
        tmp.write(
            yaml.safe_dump(settings.model_dump(), sort_keys=False).encode("utf-8")
        )
        os.environ["ENGINE_CONFIG_PATH"] = tmp.name
    run_main()


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
