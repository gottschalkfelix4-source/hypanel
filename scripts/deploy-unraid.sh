#!/bin/sh
set -eu
# Run on the Docker host after building/pulling the image.
DATA_PATH="${HYPANEL_HOST_DATA:-/mnt/user/appdata/hypanel}"
IMAGE="${HYPANEL_IMAGE:-ghcr.io/gottschalkfelix4-source/hypanel:latest}"
PORT="${HYPANEL_PORT:-8095}"
case "$DATA_PATH" in /*) ;; *) echo 'HYPANEL_HOST_DATA must be an absolute host path' >&2; exit 1;; esac
mkdir -p "$DATA_PATH"
if docker container inspect HyPanel >/dev/null 2>&1; then
  echo 'HyPanel already exists. Update it through Unraid DockerMan or Compose; this script never replaces an existing container.' >&2
  exit 1
fi
docker run -d --name HyPanel --restart unless-stopped --init \
  --security-opt=no-new-privileges:true --log-opt max-size=20m --log-opt max-file=3 \
  -p "$PORT:8080" -p "${HYPANEL_SFTP_PUBLIC_PORT:-2223}:2222" \
  -v "$DATA_PATH:/data" -v /var/run/docker.sock:/var/run/docker.sock \
  -e HYPANEL_HOST_DATA="$DATA_PATH" -e HYPANEL_RUNNER_IMAGE="$IMAGE" \
  -e HYPANEL_SFTP_PUBLIC_PORT="${HYPANEL_SFTP_PUBLIC_PORT:-2223}" \
  -e TZ=Europe/Berlin \
  -l net.unraid.docker.managed=dockerman \
  -l "net.unraid.docker.webui=http://[IP]:[PORT:8080]/" \
  -l "net.unraid.docker.icon=https://raw.githubusercontent.com/gottschalkfelix4-source/hypanel/main/frontend/icon.svg" \
  "$IMAGE"
if [ -d /boot/config/plugins/dockerMan/templates-user ]; then
  if [ -f /boot/config/plugins/dockerMan/templates-user/my-HyPanel.xml ]; then
    echo 'Existing DockerMan template kept.'
  else
    cp unraid/hypanel.xml /boot/config/plugins/dockerMan/templates-user/my-HyPanel.xml
  fi
fi
echo "HyPanel started on HTTP port $PORT. Retrieve /data/setup.key locally for initial setup."
