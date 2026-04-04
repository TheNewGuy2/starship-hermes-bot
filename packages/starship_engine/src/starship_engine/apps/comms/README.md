# Comms App

Purpose
Publishing engine facts/events to external sinks (HTTP, JSONL, etc.).

Key Modules
- `app.py`
- `publishers.py`
- `settings.py`

Configuration
Settings defaults live in `settings.py` and can be overridden by root config (e.g. `configs/bot.yaml`) or env vars.
