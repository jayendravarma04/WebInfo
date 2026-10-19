#!/usr/bin/env bash
# Run in Google Cloud Shell (free):  bash deploy/create-vm.sh
# Creates ONE always-free-eligible VM: e2-micro, 30 GB pd-standard, us-central1.
set -euo pipefail
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"          # free tier: us-west1 / us-central1 / us-east1 only
NAME="${NAME:-webinfo-vm}"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -n "$PROJECT" ] || { echo "Set a project: gcloud config set project <ID>"; exit 1; }
echo "Project=$PROJECT Zone=$ZONE VM=$NAME"
gcloud services enable compute.googleapis.com --project "$PROJECT"

if ! gcloud compute firewall-rules describe webinfo-allow-http --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute firewall-rules create webinfo-allow-http --project "$PROJECT" \
    --allow tcp:80 --target-tags webinfo --description "WebInfo agent HTTP"
fi

gcloud compute instances create "$NAME" --project "$PROJECT" --zone "$ZONE" \
  --machine-type e2-micro \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 30GB --boot-disk-type pd-standard \
  --tags webinfo \
  --no-service-account --no-scopes \
  --shielded-secure-boot \
  --metadata-from-file startup-script="$HERE/startup.sh"

IP=$(gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" \
      --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo
echo "VM created. External IP: $IP"
echo "Wait ~3 min for Docker to install, then:"
echo "  gcloud compute ssh $NAME --zone $ZONE"
echo "  and run the 'install' step from README (deploy/install.sh)."
