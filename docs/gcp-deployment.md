# GCP deployment sketch

This repo now supports a straightforward single-VM rollout:

- `starship_engine` runs continuously
- `starship_web` receives engine facts
- Slack alerts are sent from the web app
- trade tickets persist on disk
- you review tickets in the private UI before any future live execution step

## Recommended first shape

Use one Compute Engine VM first.

- process manager: `systemd`
- reverse proxy: `caddy` or `nginx`
- secrets: Secret Manager rendered into `/etc/starship-alpha/secrets`
- state/logs: persistent VM disk under `/opt/starship-alpha/app/data` and `/opt/starship-alpha/app/logs`

## VM layout

```text
/opt/starship-alpha/app
/etc/starship-alpha/starship.env
/etc/starship-alpha/secrets/
```

## Runtime env support

Both engine and web understand:

- `STARSHIP_ENV_FILE`
- `STARSHIP_SECRETS_DIR`

That lets you keep low-risk config in `starship.env` and high-risk secrets in file-per-secret paths.

## Secret rendering

Use `deploy/gcp/render-secrets.sh` as a bootstrap example. It pulls the latest version of each named secret and writes it to the file-per-secret directory expected by the app.

Example secret names:

- `STARSHIP_ADMIN_USER`
- `STARSHIP_ADMIN_PASSWORD`
- `SLACK_WEBHOOK_URL`
- `ENGINE_INGEST_SECRET`
- `ETRADE_CONSUMER_KEY`
- `ETRADE_CONSUMER_SECRET`
- `ETRADE_OAUTH_TOKEN`
- `ETRADE_OAUTH_TOKEN_SECRET`

## Systemd path

Use the unit files in `deploy/systemd/`.

Both units now point at:

- `STARSHIP_ENV_FILE=/etc/starship-alpha/starship.env`
- `STARSHIP_SECRETS_DIR=/etc/starship-alpha/secrets`

## Docker path

If you prefer containers on the VM, use:

- `deploy/docker/Dockerfile.web`
- `deploy/docker/Dockerfile.engine`
- `deploy/docker/docker-compose.gcp.yml`

The compose file keeps the web app bound to `127.0.0.1:8000` on the VM so you can put a reverse proxy in front of it.
Copy `deploy/env/.env.example` to `deploy/env/starship.env` before using the compose file.

## Suggested rollout order

1. Bring up the VM and clone the repo.
2. Install Python/venv or Docker.
3. Render secrets from Secret Manager into `/etc/starship-alpha/secrets`.
4. Start `starship-web`.
5. Start `starship-engine`.
6. Verify `/health`, `/ops/status`, Slack alert delivery, and ticket creation.
7. Put HTTPS in front of the web app.

## Intentional safety boundary

The UI now has a mock Execute review card, but there is still no live place-order path. That is intentional.
