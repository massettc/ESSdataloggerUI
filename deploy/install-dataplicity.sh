#!/usr/bin/env bash
# Install the Dataplicity remote access agent.
# This script must run as root (called via sudo by the app service account).
set -euo pipefail

echo "=== Dataplicity install ==="
curl -s https://www.dataplicity.com/3-5oxoarku.py | python
echo "=== Dataplicity install complete ==="
