#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-}"
ZONE="${ZONE:-us-west1-a}"
REGION="${REGION:-${ZONE%-*}}"
VM_NAME="${VM_NAME:-starship-hermes-web}"
ADDRESS_NAME="${ADDRESS_NAME:-starship-hermes-ip}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-small}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-30GB}"

if [[ -z "${PROJECT_ID}" ]]; then
  cat >&2 <<'USAGE'
Missing PROJECT_ID.

Run this from Google Cloud Shell like:

  PROJECT_ID="your-firebase-google-project-id" \
  bash deploy/gcp/bootstrap-stable-vm.sh

Optional overrides:

  ZONE="us-west1-a"
  VM_NAME="starship-hermes-web"
  ADDRESS_NAME="starship-hermes-ip"
  MACHINE_TYPE="e2-small"
USAGE
  exit 2
fi

echo "[starship] Project: ${PROJECT_ID}"
echo "[starship] Zone: ${ZONE}"
echo "[starship] Region: ${REGION}"
echo "[starship] VM: ${VM_NAME}"
echo "[starship] Static IP name: ${ADDRESS_NAME}"

gcloud config set project "${PROJECT_ID}"

echo "[starship] Enabling required APIs..."
gcloud services enable \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  secretmanager.googleapis.com

if gcloud compute addresses describe "${ADDRESS_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "[starship] Static IP already exists."
else
  echo "[starship] Reserving static IP..."
  gcloud compute addresses create "${ADDRESS_NAME}" --region "${REGION}"
fi

STATIC_IP="$(gcloud compute addresses describe "${ADDRESS_NAME}" --region "${REGION}" --format='value(address)')"
echo "[starship] Static IP: ${STATIC_IP}"

if gcloud compute firewall-rules describe allow-starship-web-http-https >/dev/null 2>&1; then
  echo "[starship] Firewall rule already exists."
else
  echo "[starship] Creating firewall rule for HTTP/HTTPS..."
  gcloud compute firewall-rules create allow-starship-web-http-https \
    --allow tcp:80,tcp:443 \
    --target-tags starship-web \
    --description "Allow HTTP/HTTPS to Starship Hermes web"
fi

if gcloud compute instances describe "${VM_NAME}" --zone "${ZONE}" >/dev/null 2>&1; then
  echo "[starship] VM already exists."
else
  echo "[starship] Creating VM..."
  gcloud compute instances create "${VM_NAME}" \
    --zone "${ZONE}" \
    --machine-type "${MACHINE_TYPE}" \
    --image-family ubuntu-2404-lts-amd64 \
    --image-project ubuntu-os-cloud \
    --boot-disk-size "${BOOT_DISK_SIZE}" \
    --boot-disk-type pd-balanced \
    --address "${STATIC_IP}" \
    --tags starship-web \
    --metadata enable-oslogin=TRUE
fi

cat <<NEXT

[starship] Bootstrap complete.

Save these values:

  PROJECT_ID=${PROJECT_ID}
  ZONE=${ZONE}
  VM_NAME=${VM_NAME}
  STATIC_IP=${STATIC_IP}

Next DNS step:

  Create an A record:
    Host/Name: bot
    Value:     ${STATIC_IP}

After DNS is pointed, SSH to the VM:

  gcloud compute ssh ${VM_NAME} --zone ${ZONE}

NEXT
