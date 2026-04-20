#!/usr/bin/env bash
set -euo pipefail

DEST_DIR="${1:-/etc/starship-alpha/secrets}"
mkdir -p "${DEST_DIR}"
chmod 750 "${DEST_DIR}"

SECRETS=(
  STARSHIP_ADMIN_USER
  STARSHIP_ADMIN_PASSWORD
  SLACK_WEBHOOK_URL
  ENGINE_INGEST_SECRET
  ETRADE_CONSUMER_KEY
  ETRADE_CONSUMER_SECRET
  ETRADE_OAUTH_TOKEN
  ETRADE_OAUTH_TOKEN_SECRET
)

for secret_name in "${SECRETS[@]}"; do
  echo "Rendering ${secret_name} to ${DEST_DIR}/${secret_name}"
  gcloud secrets versions access latest --secret="${secret_name}" > "${DEST_DIR}/${secret_name}"
  chmod 640 "${DEST_DIR}/${secret_name}"
done

echo "Secrets rendered to ${DEST_DIR}"
