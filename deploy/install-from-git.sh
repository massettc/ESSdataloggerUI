#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 <repo-url> [branch]"
    exit 1
fi

REPO_URL=$1
BRANCH=${2:-main}
WORK_DIR=${WORK_DIR:-/tmp/pi-network-admin-src}

if ! command -v git >/dev/null 2>&1; then
    echo "git not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y git
fi

rm -rf "$WORK_DIR"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORK_DIR"

cd "$WORK_DIR"
bash deploy/install.sh

echo "Git-based install completed from $REPO_URL ($BRANCH)."