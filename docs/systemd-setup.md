# systemd setup (engine + web)

Assumes:
- repo at `/opt/starship-alpha/app`
- venv at `/opt/starship-alpha/app/.venv`
- user `starship`
- FastAPI on `127.0.0.1:8000`

## Secret layout

Use one or both of these:
- `/etc/starship-alpha/starship.env` for plain env vars
- `/etc/starship-alpha/secrets/` for file-per-secret values

Example `/etc/starship-alpha/starship.env`:

```bash
BROKER_PROVIDER=etrade
STARSHIP_ADMIN_USER=REPLACE_ME
STARSHIP_ADMIN_PASSWORD=REPLACE_ME
ETRADE_CONSUMER_KEY=REPLACE_ME
ETRADE_CONSUMER_SECRET=REPLACE_ME
ETRADE_ENV=sandbox
ETRADE_CALLBACK_URL=oob
ETRADE_DEFAULT_ACCOUNT_ID_KEY=REPLACE_ME
SLACK_WEBHOOK_URL=REPLACE_ME
```

Example file-per-secret layout:

```text
/etc/starship-alpha/secrets/STARSHIP_ADMIN_USER
/etc/starship-alpha/secrets/STARSHIP_ADMIN_PASSWORD
/etc/starship-alpha/secrets/SLACK_WEBHOOK_URL
/etc/starship-alpha/secrets/ENGINE_INGEST_SECRET
/etc/starship-alpha/secrets/ETRADE_CONSUMER_KEY
/etc/starship-alpha/secrets/ETRADE_CONSUMER_SECRET
/etc/starship-alpha/secrets/ETRADE_OAUTH_TOKEN
/etc/starship-alpha/secrets/ETRADE_OAUTH_TOKEN_SECRET
```

Set ingest settings in `/opt/starship-alpha/app/configs/bot.yaml` under `comms`:

```yaml
comms:
  engine_ingest_url: http://127.0.0.1:8000/engine/ingest
  engine_ingest_secret: REPLACE_ME
  engine_facts_jsonl: /opt/starship-alpha/app/data/facts.jsonl
  engine_run_id: ""
```

Create the secure directories:

```bash
sudo mkdir -p /etc/starship-alpha/secrets
sudo chown root:root /etc/starship-alpha/starship.env
sudo chmod 640 /etc/starship-alpha/starship.env
sudo chown -R root:starship /etc/starship-alpha/secrets
sudo chmod 750 /etc/starship-alpha/secrets
sudo chmod 640 /etc/starship-alpha/secrets/*
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
WorkingDirectory=/opt/starship-alpha/app

Environment="PYTHONUNBUFFERED=1"
Environment="STARSHIP_ENV_FILE=/etc/starship-alpha/starship.env"
Environment="STARSHIP_SECRETS_DIR=/etc/starship-alpha/secrets"
EnvironmentFile=-/etc/starship-alpha/starship.env

ExecStart=/opt/starship-alpha/app/.venv/bin/python -m uvicorn starship_web.app:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/starship-alpha/app/data /opt/starship-alpha/app/logs

[Install]
WantedBy=multi-user.target
```

## Engine service

`/etc/systemd/system/starship-engine.service`

```ini
[Unit]
Description=Starship Alpha Engine
After=network-online.target starship-web.service
Wants=network-online.target starship-web.service

[Service]
Type=simple
User=starship
Group=starship
WorkingDirectory=/opt/starship-alpha/app

Environment="PYTHONUNBUFFERED=1"
Environment="STARSHIP_ENV_FILE=/etc/starship-alpha/starship.env"
Environment="STARSHIP_SECRETS_DIR=/etc/starship-alpha/secrets"
EnvironmentFile=-/etc/starship-alpha/starship.env

ExecStart=/opt/starship-alpha/app/.venv/bin/python -m starship_engine.runner
Restart=on-failure
RestartSec=2

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/starship-alpha/app/data /opt/starship-alpha/app/logs

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

## Ops page

Once `STARSHIP_ADMIN_USER` and `STARSHIP_ADMIN_PASSWORD` are set, the private health page is:

```text
https://your-hostname/ops/status
```
