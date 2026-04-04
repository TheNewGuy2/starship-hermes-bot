# Run Commands (monorepo)

## Local dev

From repo root:

```bash
uv sync --dev
```

Run engine (CLI):

```bash
uv run cli --signal-mode es_context --context-symbol ES --context-exchange CME --context-code ES
```

Run engine (module):

```bash
uv run python -m starship_engine.runner
```

Run web ingest (FastAPI):

```bash
uv run uvicorn starship_web.app:app --host 127.0.0.1 --port 8000
```

## Reporting

Generate a report from state logs:

```bash
uv run starship-report --help
```

Example:

```bash
uv run starship-report --input data/spx0dte_state_2026-02-02.jsonl --out reports/2026-02-02
```

## Backtesting (preferred)

Use suite configs for repeatable runs:

```bash
uv run alpha-sim configs/suites/ic_filter_phase25.yaml
```

## Configuration + Secrets

Primary config lives in `configs/bot.yaml` (copy `configs/bot.yaml.example`).

Secrets live in `.env` at the repo root:
- `TT_CLIENT_ID`
- `TT_CLIENT_SECRET`
- `TT_REFRESH_TOKEN`
- `TT_IS_TEST`
- `SLACK_WEBHOOK_URL`

The web server reads `comms.engine_ingest_secret` from `configs/bot.yaml` (or `ENGINE_INGEST_SECRET` if you still set it for back-compat).
