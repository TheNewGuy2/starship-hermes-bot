from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from starship_engine.apps.captain_log.logging import get_logger, setup_logging
from starship_engine.apps.etrade_probe.app import ETradeProbeRunner, build_probe_publisher
from starship_engine.core.settings import apply_env_overrides, load_engine_settings


def _temp_config_path(
    *,
    poll_seconds: int,
    cooldown_seconds: int,
    once: bool,
    option_roots: str,
) -> Path:
    import yaml

    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    settings = load_engine_settings(cfg_path)
    runner = settings.runner.model_dump()
    runner["etrade_poll_seconds"] = poll_seconds
    runner["etrade_signal_cooldown_seconds"] = cooldown_seconds
    runner["etrade_run_once"] = once
    runner["etrade_option_roots"] = option_roots
    settings.runner = settings.runner.model_validate(runner)
    with tempfile.NamedTemporaryFile(
        prefix="starship-config-", suffix=".yaml", delete=False
    ) as tmp:
        tmp.write(
            yaml.safe_dump(settings.model_dump(), sort_keys=False).encode("utf-8")
        )
        return Path(tmp.name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="starship_engine.probe_cli",
        description="Run the E*TRADE SPX probe and optionally send Slack directly.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe cycle and exit.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep polling instead of running once.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Polling interval for loop mode.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=300,
        help="Minimum delay before repeating an identical alert.",
    )
    parser.add_argument(
        "--option-roots",
        default="SPX,SPXW,XSP",
        help="Comma-separated option roots to try in order.",
    )
    parser.add_argument(
        "--direct-slack",
        dest="direct_slack",
        action="store_true",
        help="Send Slack directly from the command.",
    )
    parser.add_argument(
        "--no-direct-slack",
        dest="direct_slack",
        action="store_false",
        help="Do not send Slack directly.",
    )
    parser.set_defaults(direct_slack=True)
    parser.add_argument(
        "--publish-web",
        dest="publish_web",
        action="store_true",
        help="Also publish facts to the configured web ingest endpoint.",
    )
    parser.add_argument(
        "--no-publish-web",
        dest="publish_web",
        action="store_false",
        help="Do not publish to the web ingest endpoint.",
    )
    parser.set_defaults(publish_web=False)
    parser.add_argument(
        "--write-jsonl",
        dest="write_jsonl",
        action="store_true",
        help="Append facts to the configured JSONL path.",
    )
    parser.add_argument(
        "--no-write-jsonl",
        dest="write_jsonl",
        action="store_false",
        help="Do not append facts to the configured JSONL path.",
    )
    parser.set_defaults(write_jsonl=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    once = True
    if args.loop:
        once = False
    elif args.once:
        once = True

    config_path = _temp_config_path(
        poll_seconds=args.poll_seconds,
        cooldown_seconds=args.cooldown_seconds,
        once=once,
        option_roots=args.option_roots,
    )
    os.environ["ENGINE_CONFIG_PATH"] = str(config_path)

    settings = apply_env_overrides(load_engine_settings(config_path), dict(os.environ))
    setup_logging(
        level_name=settings.runner.log_level,
        log_file=settings.runner.log_file,
        log_daily=settings.runner.log_daily,
        log_daily_backup_count=settings.runner.log_daily_backup_count,
    )
    log = get_logger("starship_engine.probe_cli")
    runner = ETradeProbeRunner(
        engine_settings=settings,
        runtime={"mode": "etrade_probe"},
        log=log,
    )
    runner.publisher = build_probe_publisher(
        engine_settings=settings,
        direct_slack=args.direct_slack,
        publish_web=args.publish_web,
        write_jsonl=args.write_jsonl,
    )
    runner.run()


if __name__ == "__main__":
    main()
