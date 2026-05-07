#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/starship-alpha/app}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker/docker-compose.gcp.yml}"
WEB_SERVICE_NAME="${WEB_SERVICE_NAME:-starship-web}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"

cd "$APP_DIR"

echo "[starship] updating ${WEB_SERVICE_NAME} from ${COMPOSE_FILE}"
docker compose -f "$COMPOSE_FILE" up -d --build "$WEB_SERVICE_NAME"

echo "[starship] service status"
docker compose -f "$COMPOSE_FILE" ps

echo "[starship] health check ${HEALTH_URL}"
curl --fail --silent --show-error "$HEALTH_URL"
echo
