#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/starship-alpha/app}"
REPO_URL="${REPO_URL:-https://github.com/TheNewGuy2/starship-hermes-bot.git}"
BRANCH="${BRANCH:-main}"
BOT_DOMAIN="${BOT_DOMAIN:-}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker/docker-compose.gcp.yml}"

echo "[starship] App dir: ${APP_DIR}"
echo "[starship] Repo: ${REPO_URL}"
echo "[starship] Branch: ${BRANCH}"

echo "[starship] Installing OS packages..."
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git caddy

if ! command -v docker >/dev/null 2>&1; then
  echo "[starship] Installing Docker from Docker's Ubuntu repository..."
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "[starship] Docker already installed."
fi

sudo mkdir -p "$(dirname "${APP_DIR}")"

if [[ -d "${APP_DIR}/.git" ]]; then
  echo "[starship] Repo already exists; updating..."
  sudo git -C "${APP_DIR}" fetch origin
  sudo git -C "${APP_DIR}" checkout "${BRANCH}"
  sudo git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
  echo "[starship] Cloning repo..."
  sudo git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"

echo "[starship] Creating runtime directories..."
sudo mkdir -p deploy/env/secrets data logs
sudo chmod 700 deploy/env/secrets

if [[ ! -f deploy/env/starship.env ]]; then
  echo "[starship] Creating deploy/env/starship.env..."
  sudo tee deploy/env/starship.env >/dev/null <<'EOF'
STARSHIP_MAX_SIGNAL_AGE_MINUTES=60
STARSHIP_MAX_PREVIEW_AGE_MINUTES=15
STARSHIP_MAX_SNAPSHOT_AGE_MINUTES=15
STARSHIP_EXECUTION_MODE=dry_run
STARSHIP_ALLOW_LIVE_EXECUTION=false
BROKER_PROVIDER=etrade
ETRADE_ENV=live
ETRADE_CALLBACK_URL=oob
EOF
else
  echo "[starship] deploy/env/starship.env already exists; leaving it unchanged."
fi

echo "[starship] Starting starship-web..."
sudo docker compose -f "${COMPOSE_FILE}" up -d --build starship-web
sudo docker compose -f "${COMPOSE_FILE}" ps

echo "[starship] Local health check..."
curl --fail --silent --show-error http://127.0.0.1:8000/health
echo

if [[ -n "${BOT_DOMAIN}" ]]; then
  echo "[starship] Configuring Caddy for ${BOT_DOMAIN}..."
  sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
${BOT_DOMAIN} {
    reverse_proxy 127.0.0.1:8000
}
EOF
  sudo systemctl reload caddy
  echo "[starship] HTTPS check. This can fail until DNS points ${BOT_DOMAIN} to this VM."
  curl --fail --silent --show-error "https://${BOT_DOMAIN}/health" || true
  echo
else
  cat <<'NEXT'
[starship] BOT_DOMAIN was not set, so Caddy was not configured yet.

After DNS is ready, run:

  BOT_DOMAIN="bot.yourdomain.com" bash deploy/gcp/setup-vm-app.sh

NEXT
fi

cat <<'DONE'

[starship] VM app setup complete.

Next private step:
  Add secrets in deploy/env/secrets, then restart:

    cd /opt/starship-alpha/app
    sudo docker compose -f deploy/docker/docker-compose.gcp.yml restart starship-web

DONE
