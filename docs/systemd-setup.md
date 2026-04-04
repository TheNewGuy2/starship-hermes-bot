# systemd setup (engine + web)

Assumes:
- repo at `/opt/starship-alpha`
- uv venv at `/opt/starship-alpha/.venv`
- user `starship`
- FastAPI on `127.0.0.1:8000`

## Environment file (recommended)

Create `/etc/starship-alpha/starship.env`:

```
TT_CLIENT_ID=REPLACE_ME
TT_CLIENT_SECRET=REPLACE_ME
TT_REFRESH_TOKEN=REPLACE_ME
TT_IS_TEST=false
SLACK_WEBHOOK_URL=REPLACE_ME
```

Set ingest settings in `/opt/starship-alpha/configs/bot.yaml` under `comms`:

```
comms:
  engine_ingest_url: http://127.0.0.1:8000/engine/ingest
  engine_ingest_secret: REPLACE_ME
  engine_facts_jsonl: /opt/starship-alpha/data/facts.jsonl
  engine_run_id: ""
```

Ensure it’s readable by systemd:

```bash
sudo mkdir -p /etc/starship-alpha
sudo chown root:root /etc/starship-alpha/starship.env
sudo chmod 640 /etc/starship-alpha/starship.env
```

## Web service

`/etc/systemd/system/starship-web.service`

```ini
[Unit]
Description=Starship Alpha Web (FastAPI ingest + Slack)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=starship
Group=starship
WorkingDirectory=/opt/starship-alpha

Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/etc/starship-alpha/starship.env

ExecStart=/opt/starship-alpha/.venv/bin/uvicorn starship_web.app:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/starship-alpha/data

[Install]
WantedBy=multi-user.target
```

## Engine service

`/etc/systemd/system/starship-engine.service`

```ini
[Unit]
Description=Starship Alpha Engine
After=network-online.target starship-web.service
Wants=network-online.target

[Service]
Type=simple
User=starship
Group=starship
WorkingDirectory=/opt/starship-alpha

Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/etc/starship-alpha/starship.env

ExecStart=/opt/starship-alpha/.venv/bin/python -m starship_engine.runner
Restart=on-failure
RestartSec=2

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/starship-alpha/data

[Install]
WantedBy=multi-user.target
```

## Enable / start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now starship-web.service
sudo systemctl enable --now starship-engine.service
```

## Logs

```bash
journalctl -u starship-web -f
journalctl -u starship-engine -f
```
