from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from starship_engine.domain.alpha_sim_lab.suite_runner import (
    run_suite as run_suite_exec,
)

alpha_sim_app = typer.Typer(help="Alpha Sim Lab (research)")


@alpha_sim_app.command("run-suite")
def alpha_sim_run_suite(
    config_path: str = typer.Argument(..., help="Path to suite YAML config"),
    outdir: Optional[str] = typer.Option(None, help="Output base directory"),
    only: Optional[str] = typer.Option(None, help="Only run a single test name"),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first error"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only"),
) -> None:
    out = run_suite_exec(
        Path(config_path),
        outdir=Path(outdir) if outdir else None,
        only=only,
        fail_fast=fail_fast,
        dry_run=dry_run,
    )
    print(f"Suite output: {out}")


def main() -> None:
    alpha_sim_app()


if __name__ == "__main__":
    main()
