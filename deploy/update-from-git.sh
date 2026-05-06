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

# Try to fetch the ref as a branch from origin directly to populate FETCH_HEAD.
# This works on shallow clones where 'git fetch --all' may not create tracking refs.
if sudo git fetch origin "$REF" 2>/dev/null; then
    sudo git checkout -B "$REF" FETCH_HEAD
elif sudo git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
    sudo git checkout -B "$REF" "origin/$REF"
else
    # REF is a tag or specific commit
    sudo git checkout "$REF"
fi

sudo bash deploy/install.sh

if [[ -f VERSION ]]; then
    echo "Update complete. Running version $(cat VERSION) from ref $REF"
else
    echo "Update complete from ref $REF"
fi
