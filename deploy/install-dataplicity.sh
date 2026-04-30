#!/usr/bin/env bash
# Install the Dataplicity remote access agent.
# Usage: sudo bash install-dataplicity.sh <install-url>
# The install URL is your account-specific enrollment URL from the Dataplicity dashboard.
# This script must run as root (called via sudo by the app service account).
set -euo pipefail

INSTALL_URL="${1:-}"
if [[ -z "$INSTALL_URL" ]]; then
    echo "ERROR: Dataplicity install URL not provided."
    echo "Set PI_ADMIN_DATAPLICITY_INSTALL_URL in /etc/pi-network-admin/app.env and restart the service."
    exit 1
fi

echo "=== Dataplicity install ==="
curl -s "$INSTALL_URL" | python
echo "=== Dataplicity install complete ==="
