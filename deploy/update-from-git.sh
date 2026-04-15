#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/pi-network-admin}
REF=${1:-main}

if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "No git checkout found at $APP_DIR"
    exit 1
fi

git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true
sudo git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true

cd "$APP_DIR"
sudo git fetch --all --tags --prune

if sudo git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
    sudo git checkout "$REF"
    sudo git pull --ff-only origin "$REF"
else
    sudo git checkout "$REF"
fi

sudo bash deploy/install.sh

if [[ -f VERSION ]]; then
    echo "Update complete. Running version $(cat VERSION) from ref $REF"
else
    echo "Update complete from ref $REF"
fi
