# GCP stable deployment

This repo now supports a clean two-lane model:

- stable lane: hosted on Google Cloud
- testing lane: your local machine plus tunnel

For the current TradingView -> Python -> E*TRADE bridge, the stable lane only needs `starship_web`.

## Recommended shape right now

Use one Compute Engine VM first.

Why this is the right fit for the current app:

- the app stores E*TRADE session state on disk
- trade tickets are file-backed
- Pine bridge states are file-backed
- audit logs and web logs are file-backed

That means the current app wants durable local storage. A VM is the lowest-risk hosted target because it matches the way the app already works.

## Where Firebase fits

Firebase is still a good ecosystem choice for this project, but not as the primary compute target yet.

Today:

- Firebase Hosting is best when you want static assets or rewrites to supported server backends
- Cloud Run is great for stateless containers

But this app is not fully stateless yet. Until we move tickets, Pine bridge state, and E*TRADE token storage out of the local filesystem, Compute Engine is the safer stable deployment target.

Practical recommendation:

- use Google Cloud Compute Engine for the stable app
- keep the Firebase project for the broader GCP environment, billing, DNS, or future frontend work
- revisit Firebase Hosting + Cloud Run later if we refactor state into Firestore, Cloud SQL, or Cloud Storage

## Repo files for the GCP lane

- Docker compose stack: [deploy/docker/docker-compose.gcp.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/docker/docker-compose.gcp.yml:1)
- Web image: [deploy/docker/Dockerfile.web](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/docker/Dockerfile.web:1)
- Remote VM deploy script: [deploy/gcp/deploy-web-vm.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/deploy-web-vm.sh:1)
- Secret rendering helper: [deploy/gcp/render-secrets.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/render-secrets.sh:1)
- CI workflow: [.github/workflows/ci.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/ci.yml:1)
- Stable deploy workflow: [.github/workflows/deploy-gcp-web.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/deploy-gcp-web.yml:1)

## Runtime storage on the VM

The app now supports:

- `STARSHIP_DATA_DIR`
- `STARSHIP_LOG_DIR`

The GCP compose file sets those explicitly inside the container:

- `/opt/starship-alpha/app/data`
- `/opt/starship-alpha/app/logs`

That keeps these stable across container restarts:

- `etrade_session.json`
- Pine bridge state files
- trade tickets
- ticket audit logs
- web logs

## VM layout

Recommended layout:

```text
/opt/starship-alpha/app
/opt/starship-alpha/app/data
/opt/starship-alpha/app/logs
/opt/starship-alpha/app/deploy/env/starship.env
/opt/starship-alpha/app/deploy/env/secrets/
```

## First-time VM setup

1. Create a small Ubuntu Compute Engine VM.
2. Install Docker and Docker Compose plugin.
3. Clone this repo to:

```text
/opt/starship-alpha/app
```

4. Copy:

- [deploy/env/.env.example](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/env/.env.example:1)

to:

```text
/opt/starship-alpha/app/deploy/env/starship.env
```

5. Fill in the low-risk env values in `starship.env`.
6. Create:

```text
/opt/starship-alpha/app/deploy/env/secrets
```

7. Put the sensitive values there as file-per-secret, or use [deploy/gcp/render-secrets.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/render-secrets.sh:1) to render them from Secret Manager.

Example secret names:

- `STARSHIP_ADMIN_USER`
- `STARSHIP_ADMIN_PASSWORD`
- `TRADINGVIEW_WEBHOOK_SECRET`
- `SLACK_WEBHOOK_URL`
- `ENGINE_INGEST_SECRET`
- `ETRADE_CONSUMER_KEY`
- `ETRADE_CONSUMER_SECRET`
- `ETRADE_OAUTH_TOKEN`
- `ETRADE_OAUTH_TOKEN_SECRET`

8. Start the stable web app once manually:

```bash
cd /opt/starship-alpha/app
docker compose -f deploy/docker/docker-compose.gcp.yml up -d --build starship-web
curl http://127.0.0.1:8000/health
```

9. Put HTTPS in front of it.

Recommended:

- Caddy or Nginx on the VM
- or a Google HTTPS load balancer later if you want more infrastructure

## GitHub Actions auto-deploy

The stable deploy workflow is:

- [.github/workflows/deploy-gcp-web.yml](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/deploy-gcp-web.yml:1)

What it does:

1. waits for `CI` to pass on `main`
2. authenticates to Google Cloud using Workload Identity Federation
3. SSHes to the stable VM with `gcloud compute ssh`
4. runs:
   - `git fetch`
   - `git checkout main`
   - `git pull --ff-only`
   - [deploy/gcp/deploy-web-vm.sh](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/deploy/gcp/deploy-web-vm.sh:1)

## GitHub secrets for deploy

Add these repository secrets:

- `GCP_PROJECT_ID`
- `GCP_ZONE`
- `GCP_INSTANCE`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`

Optional:

- `GCP_VM_APP_DIR`
  - default expected by the workflow is `/opt/starship-alpha/app`

## Recommended IAM shape

Use Workload Identity Federation between GitHub Actions and Google Cloud instead of storing a long-lived service account key in GitHub.

The service account used by the workflow should be allowed to:

- access the target project
- SSH into the Compute Engine instance
- use OS Login if your VM is configured for it

## Daily usage

### Stable lane

Use the hosted VM-backed URL for your dependable version.

Morning checklist:

1. open the stable web app
2. confirm `/health`
3. reconnect E*TRADE if needed in `/broker/etrade`
4. confirm TradingView points to the stable webhook URL
5. watch `/pine-bridge` and `/tickets`

### Testing lane

Keep using local plus tunnel while we are experimenting.

Testing checklist:

1. make local code changes
2. run the app locally
3. start the tunnel
4. point TradingView to the tunnel webhook
5. validate behavior before pushing

## Release flow

1. test locally with the tunnel
2. commit changes
3. push to `main`
4. [CI](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/ci.yml:1) runs
5. [deploy-gcp-web](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/.github/workflows/deploy-gcp-web.yml:1) updates the stable VM

## Important boundary

The current hosted stable lane is for the web app and Pine bridge workflow.

That means:

- stable webhook URL
- stable E*TRADE review UI
- stable Pine bridge state and ticket persistence

The signal-engine experimentation lane can stay local until we intentionally move more of the stack into hosted infrastructure.
