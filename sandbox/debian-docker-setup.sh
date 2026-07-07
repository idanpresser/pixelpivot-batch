#!/bin/bash
# Runs inside Debian WSL in Windows Sandbox.
# Installs Docker CE, loads PixelPivot images, starts services.
set -euo pipefail

AIRGAP_DIR="${1:-/mnt/c/airgap}"
PROJECT_DIR="/mnt/c/pixelpivot"

log() { echo "[DEBIAN] $*"; }

# ── 1. System update + Docker CE ────────────────────────────────────────────
log "Installing Docker CE..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release iptables

# Add Docker apt repo
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -qq
apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-compose-plugin

# ── 2. Docker daemon config (IPv4, no iptables conflict with WSL) ────────────
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
    "iptables": false,
    "ip": "0.0.0.0",
    "ipv6": false,
    "log-driver": "json-file",
    "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF

# ── 3. Start dockerd ─────────────────────────────────────────────────────────
log "Starting Docker daemon..."
dockerd --host=unix:///var/run/docker.sock &>/var/log/dockerd.log &
DOCKERD_PID=$!

# Wait for socket
for i in $(seq 1 30); do
    docker info &>/dev/null && break
    sleep 1
done
docker info &>/dev/null || { log "ERROR: dockerd failed to start"; cat /var/log/dockerd.log; exit 1; }
log "Docker ready (pid $DOCKERD_PID)"

# ── 4. Load images ───────────────────────────────────────────────────────────
if [ -f "${AIRGAP_DIR}/pixelpivot-app.tar.gz" ]; then
    log "Loading app images from air-gap bundle..."
    docker load < "${AIRGAP_DIR}/pixelpivot-app.tar.gz"
    docker load < "${AIRGAP_DIR}/postgres.tar.gz"
else
    log "No air-gap bundle found — pulling from Docker Hub..."
    # Images must be built first; pull base images at minimum
    log "WARNING: Run 'docker-compose build' in /pixelpivot to build app images."
fi

# ── 5. Mount project and start services ─────────────────────────────────────
log "Starting PixelPivot services..."
mkdir -p /pixelpivot
mount --bind "${PROJECT_DIR}" /pixelpivot 2>/dev/null || \
    ln -sfn "${PROJECT_DIR}" /pixelpivot

cd /pixelpivot
docker compose up -d

log "Services started:"
docker compose ps
log ""
log "  API:  http://localhost:8000"
log "  GUI:  http://localhost:8503"
log "  DB:   postgresql://pixelpivot:pixelpivot@localhost:5433/pixelpivot"
