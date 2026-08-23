# Permanent hosting plan

The selected permanent hosting path for the current Hermes/Pine Bridge app is:

```text
Google Cloud Compute Engine + starship-hermes.app.
```

Chosen production domain:

```text
starship-hermes.app
```

Because `.app` is HTTPS-required, the production service must be reachable over HTTPS before browsers will reliably load it. Caddy handles this automatically once DNS points to the VM and ports `80` and `443` are reachable.

## Why GCP now

The current hosted piece is `starship_web`, not the full local research stack. It needs:

- a stable HTTPS URL for TradingView webhooks
- durable VM-backed storage that matches the current file-backed app
- persistent disk storage for E*TRADE session files, trade tickets, Pine bridge states, audit logs, and web logs
- direct control over Caddy, firewalling, Docker Compose, and future automation

This repo already includes the GCP deployment lane:

- [deploy/docker/Dockerfile.web](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/docker/Dockerfile.web)
- [deploy/docker/docker-compose.gcp.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/docker/docker-compose.gcp.yml)
- [deploy/gcp/bootstrap-stable-vm.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/bootstrap-stable-vm.sh)
- [deploy/gcp/setup-vm-app.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/setup-vm-app.sh)
- [docs/gcp-deployment.md](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/docs/gcp-deployment.md)

The GCP Docker Compose lane maps repo directories into the container and points the app to:

- `STARSHIP_DATA_DIR=/opt/starship-alpha/app/data`
- `STARSHIP_LOG_DIR=/opt/starship-alpha/app/logs`

Only files written under those paths should be treated as durable hosted state.

## First deploy checklist

1. Create or choose the Google Cloud project.
2. Run [deploy/gcp/bootstrap-stable-vm.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/bootstrap-stable-vm.sh) from Cloud Shell.
3. Create the DNS `A` record for `starship-hermes.app` using the static IP printed by the bootstrap script.
4. SSH to the VM.
5. Run [deploy/gcp/setup-vm-app.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/setup-vm-app.sh) with `BOT_DOMAIN="starship-hermes.app"`.
6. Add the required secrets in `/opt/starship-alpha/app/deploy/env/secrets`.
7. Restart `starship-web`.
8. Confirm `/health` returns `{"ok": true}`.
9. Open `/broker/etrade` and complete the E*TRADE connection.
10. Update TradingView webhook URL to `https://starship-hermes.app/webhooks/tradingview`.
11. Open `/pine-bridge` and `/pine-bridge/calibration` during the next live alert cycle.

Cloud Shell bootstrap command:

```bash
PROJECT_ID="YOUR_GOOGLE_CLOUD_PROJECT_ID" \
DOMAIN_NAME="starship-hermes.app" \
bash deploy/gcp/bootstrap-stable-vm.sh
```

## Domain and DNS

Canonical production URL:

```text
https://starship-hermes.app
```

TradingView webhook URL:

```text
https://starship-hermes.app/webhooks/tradingview
```

Google Cloud Compute Engine DNS:

1. Reserve a static external IPv4 address for the VM.
2. In your DNS provider, create:

```text
@     A      YOUR_GCP_STATIC_IP
www   CNAME  starship-hermes.app
```

3. Keep the DNS record unproxied/DNS-only until Caddy has successfully issued TLS certificates.
4. Ensure the VM firewall allows inbound `80/tcp` and `443/tcp`.
5. Configure Caddy with:

```text
starship-hermes.app {
    reverse_proxy 127.0.0.1:8000
}
```

From this repo on Windows, check DNS propagation with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_starship_domain_dns.ps1 -ExpectedIp YOUR_GCP_STATIC_IP
```

Optional `www` redirect after the `www` DNS record exists:

```text
www.starship-hermes.app {
    redir https://starship-hermes.app{uri} permanent
}
```

## Required hosted secrets

Set these in the hosting provider dashboard. Do not commit them to the repo.

```text
STARSHIP_ADMIN_USER
STARSHIP_ADMIN_PASSWORD
TRADINGVIEW_WEBHOOK_SECRET
ETRADE_DEFAULT_ACCOUNT_ID_KEY
ETRADE_CONSUMER_KEY
ETRADE_CONSUMER_SECRET
```

Useful optional secrets:

```text
SLACK_WEBHOOK_URL
ENGINE_INGEST_SECRET
```

## Stable routes

After deploy, the stable hosted base URL should expose:

```text
/health
/broker/etrade
/tickets
/pine-bridge
/pine-bridge/calibration
/ops/status
/webhooks/tradingview
```

## TradingView webhook

Use:

```text
https://starship-hermes.app/webhooks/tradingview
```

The Pine-side `tvBridgeSecret` must match `TRADINGVIEW_WEBHOOK_SECRET` on the hosted service.

## Render fallback

Use Render if we later want a simpler dashboard-managed deployment instead of direct VM control.

The existing Render path is documented here:

- [render.yaml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/render.yaml)
- [docs/render-stable-deployment.md](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/docs/render-stable-deployment.md)

For now, GCP is the selected stable lane because it gives us direct control over the VM, Caddy, persistent local files, and future automation.
