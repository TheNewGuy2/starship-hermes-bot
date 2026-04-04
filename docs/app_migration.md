# App Architecture Migration Plan

Goal: introduce a Django-style `INSTALLED_APPS` model with a minimal app loader, then migrate subsystems incrementally without changing behavior.

Note (2026-02-04): Pausing migration work to focus on other priorities. Keep `pipeline/` in place for now and revisit when ready.

## Principles

1. No behavioral change during scaffold phases.
2. Migrate in small slices with parity checks.
3. Keep wiring reversible until confident.

## Phase 0: Scaffold (Done)

- Add `core/apps.py` loader and `core/context.py`.
- Create `apps/` namespace with placeholder `app.py` for each subsystem.

## Phase 1: Install Apps (No Behavior Change)

1. Add `INSTALLED_APPS` list in a single location (runner or config).
2. Call `load_apps()` during startup and store the list.
3. Do not call any app hooks yet.

## Phase 2: Read-Only Apps (Low Risk)

Migrate subsystems that only log or notify:

- `captain_log`
- `comms`
- `sentinel`

Steps:

1. Move logic into `apps/<name>/`.
2. Implement `on_candle` / `on_event` hooks.
3. Route existing calls through the app.
4. Validate outputs match current logs/messages.

## Phase 3: Analytics + Diagnostics

Migrate subsystems that annotate or compute but do not gate:

- `trend_risk`
- state logging (if separated)

Steps:

1. Move computations into app modules.
2. Ensure JSONL output and metrics are unchanged.
3. Add back-compat tests for score outputs.

## Phase 4: Gating + Decision Logic

Migrate decision-critical components last:

- `heston`
- `exit_planner`

Steps:

1. Move gate logic into app modules.
2. Keep old entry points as wrappers until parity is proven.
3. Add regression tests comparing new vs old gate output.

## Phase 5: Config Consolidation

1. Each app exposes a Pydantic `Settings` model.
2. `EngineSettings` aggregates app settings.
3. Migrate env vars gradually to structured config (YAML/TOML) with env overrides.

## Suggested Order

1. `captain_log`
2. `comms`
3. `sentinel`
4. `trend_risk`
5. `exit_planner`
6. `heston`
7. `stream`, `auth`, `directives`, `context_overnight`

## Rollback Plan

If any migration phase changes behavior:

1. Revert wiring to the previous call path.
2. Keep the app module in place for later iteration.
3. Add a regression test before retrying.
