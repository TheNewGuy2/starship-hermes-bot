# Layered Architecture Plan (monorepo + uv)

Goal: split the current codebase into clear layers while keeping a single uv environment.

## Target layout

```
/opt/starship-alpha/
  pyproject.toml
  uv.lock
  packages/
    starship_shared/
      pyproject.toml
      src/starship_shared/
        __init__.py
        schemas.py
        signing.py
        enums.py
    starship_engine/
      pyproject.toml
      src/starship_engine/
        __init__.py
        run.py
        emit.py
    starship_web/
      pyproject.toml
      src/starship_web/
        __init__.py
        app.py
        slack.py
  data/
    starship.db
    facts.jsonl
```

## Layer responsibilities

- `starship_shared`:
  - Shared schemas, enums, and signing helpers (HMAC, payload formats).
  - No direct dependencies on engine or web.

- `starship_engine`:
  - Core engine/runner/pipeline logic.
  - Emits structured facts/signals/events.
  - For now this can write to JSONL and/or post to web ingest.
  - Submodules:
    - `sentinel/` — regime/heartbeat/guardrails
    - `directives/` — trade policy and guardrails
    - `captain_log/` — logging/state logs

- `starship_web`:
  - Slack notifier for now.
  - Later expands to Slack bot (interactive commands, inputs, persistence).

- `starship_simlab`:
  - Research/backtesting utilities (suite configs, charts).

- `starship_reports`:
  - Report generator reading JSONL logs and producing charts/summaries.

## Phased migration

1) **Document boundaries (now)**
   - Keep current repo, add this doc and align naming.
   - Start refactors that clarify pipeline vs core.

2) **Split into packages**
   - Move shared schemas/signing into `starship_shared`.
   - Move engine core (runner, pipeline, indicators, etc.) into `starship_engine`.
   - Stand up `starship_web` with FastAPI + Slack endpoints for ingest.

3) **Wire data flow**
   - Engine posts facts/signals/events to web ingest.
   - Web stores to JSONL (and DB later) and handles Slack.

4) **Slack bot expansion**
   - Add slash commands / interactions.
   - Add persistence as needed (MariaDB).

## Systemd outline

- `starship-web.service` -> FastAPI/uvicorn (127.0.0.1:8000)
- `starship-engine.service` -> engine runner
- `cloudflared.service` -> tunnel

## Notes

- The DB is optional until user inputs or persistent reporting are needed.
- Keep artifacts (jsonl/images/reports) on disk even after DB adoption.
