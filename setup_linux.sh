#!/usr/bin/env bash
set -euo pipefail

# One-shot setup for Docksmith on Ubuntu/Debian.
# Installs required packages and imports the Alpine base image.

if [[ "${EUID}" -eq 0 ]]; then
  APT_PREFIX=""
else
  APT_PREFIX="sudo"
fi

echo "[1/4] Installing OS packages..."
${APT_PREFIX} apt update
${APT_PREFIX} apt install -y python3 python3-pip util-linux

echo "[2/5] Syncing repository changes (if git remote exists)..."
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git remote get-url origin >/dev/null 2>&1; then
    git pull --ff-only || true
  fi
fi

echo "[3/5] Making CLI executable..."
chmod +x docksmith.py

echo "[4/5] Importing base image (idempotent)..."
python3 setup_base_image.py

echo "[5/5] Done. You can now build and run images."
echo "Example: python3 docksmith.py build -t myapp:latest ./sample_app"
