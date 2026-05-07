# Stable Hosting on Render

This repo now supports a clean split:

- `main` = stable hosted web app
- local unpushed work / feature branches = testing with your tunnel

For the current TradingView -> Python -> E*TRADE bridge workflow, the only piece that must be hosted is `starship_web`. The strategy engine can stay local until you want a fully hosted candle/signal stack.

## Why Render for the stable lane

Render is the best fit for the current shape because it gives you:

- a stable HTTPS URL without Cloudflare tunnel
- GitHub-connected Docker deploys
- persistent disk storage for E*TRADE session state, tickets, bridge states, and logs
- a simple path to custom domains later

This repo includes:

- [render.yaml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/render.yaml)
- [.github/workflows/ci.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/ci.yml)

The blueprint is set to `autoDeployTrigger: checksPass`, which means:

1. you push to `main`
2. GitHub Actions CI runs
3. Render deploys only after the checks pass

## What persists on disk

The hosted app now supports:

- `STARSHIP_DATA_DIR`
- `STARSHIP_LOG_DIR`

On Render, the blueprint mounts a disk at `/var/data/starship` and points the app to:

- `/var/data/starship/data`
- `/var/data/starship/logs`

That keeps these files across deploys and restarts:

- E*TRADE session tokens
- trade tickets
- ticket audit logs
- Pine bridge state
- web logs

## First-time setup

1. Push this repo to GitHub.
2. Create a new Render Blueprint service from the repo.
3. Let Render read [render.yaml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/render.yaml).
4. Confirm the web service name and disk settings.
5. Set the unsynced secrets in Render.

Required secrets:

- `STARSHIP_ADMIN_USER`
- `STARSHIP_ADMIN_PASSWORD`
- `TRADINGVIEW_WEBHOOK_SECRET`
- `ETRADE_DEFAULT_ACCOUNT_ID_KEY`
- `ETRADE_CONSUMER_KEY`
- `ETRADE_CONSUMER_SECRET`

Optional but useful:

- `SLACK_WEBHOOK_URL`
- `ENGINE_INGEST_SECRET`

Fixed env values already declared in the blueprint:

- `ETRADE_CALLBACK_URL=oob`
- `ETRADE_ENV=live`
- `PINE_BRIDGE_POINT_VALUE=100`

## After the first deploy

Your stable base URL will be something like:

```text
https://starship-web-stable.onrender.com
```

Useful routes:

- `/`
- `/health`
- `/broker/etrade`
- `/tickets`
- `/pine-bridge`
- `/ops/status`
- `/webhooks/tradingview`

## E*TRADE on the hosted service

The hosted app still needs a valid E*TRADE session.

Normal pattern:

1. open `/broker/etrade`
2. start the authorization flow
3. complete the verifier flow
4. confirm accounts load

Because the token files are on the persistent disk, they survive deploys. You may still need to renew or reconnect depending on E*TRADE token lifetime.

## TradingView webhook for the stable lane

Once the stable site is up, set the TradingView alert webhook URL to:

```text
https://YOUR-RENDER-URL/webhooks/tradingview
```

Keep the bridge secret in the Pine script equal to `TRADINGVIEW_WEBHOOK_SECRET` in Render.

## Daily workflow

### Stable production-like lane

Use the hosted Render URL when you want the dependable version.

Morning checklist:

1. open the hosted site
2. confirm `/health`
3. reconnect E*TRADE if needed
4. confirm TradingView is pointing to the hosted webhook
5. monitor `/pine-bridge` and `/tickets`

### Testing lane

Keep using your local machine plus tunnel when we are experimenting.

Testing checklist:

1. change code locally
2. run local app
3. start the tunnel
4. point TradingView to the tunnel URL
5. validate behavior before pushing

## Release flow

1. Test locally with the tunnel.
2. Commit the change.
3. Push to `main`.
4. GitHub Actions runs [ci.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/ci.yml).
5. Render deploys automatically after checks pass.
6. The hosted stable URL becomes the new stable version.

## Good next follow-ups

- add a second hosted staging service later if you want a true remote test environment
- add a small bridge activity/errors page in the UI
- move more operational secrets into the provider’s secret manager or equivalent if you outgrow dashboard-managed secrets
