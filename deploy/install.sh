#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/pi-network-admin
CONFIG_DIR=/etc/pi-network-admin
SERVICE_NAME=pi-network-admin.service
WATCHDOG_SERVICE_NAME=pi-network-failover.service
USER_NAME=pi-network-admin

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

# ── Preflight: ensure NetworkManager is installed and active ──
if ! command -v nmcli >/dev/null 2>&1; then
    echo "NetworkManager (nmcli) not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y network-manager
fi

if systemctl is-active --quiet dhcpcd 2>/dev/null; then
    echo "Disabling dhcpcd in favour of NetworkManager..."
    sudo systemctl disable --now dhcpcd
fi

if ! systemctl is-active --quiet NetworkManager; then
    echo "Enabling NetworkManager..."
    sudo systemctl enable --now NetworkManager
fi

echo "Preflight OK: NetworkManager is active ($(nmcli --version))."

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    sudo useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi

sudo mkdir -p "$APP_DIR" "$CONFIG_DIR" /var/log/pi-network-admin
sudo cp -a "$REPO_DIR"/. "$APP_DIR/"
sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR" /var/log/pi-network-admin

python3 -m venv "$APP_DIR/.venv"
source "$APP_DIR/.venv/bin/activate"
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"
if [[ ! -f "$CONFIG_DIR/app.env" ]]; then
    sudo cp "$APP_DIR/config/app.env.example" "$CONFIG_DIR/app.env"
    echo "Created $CONFIG_DIR/app.env from template."
else
    echo "Keeping existing $CONFIG_DIR/app.env"
fi
sudo cp "$APP_DIR/config/sudoers.pi-network-admin" /etc/sudoers.d/pi-network-admin
sudo chmod 440 /etc/sudoers.d/pi-network-admin
sudo cp "$APP_DIR/systemd/$SERVICE_NAME" /etc/systemd/system/$SERVICE_NAME
sudo cp "$APP_DIR/systemd/$WATCHDOG_SERVICE_NAME" /etc/systemd/system/$WATCHDOG_SERVICE_NAME
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME $WATCHDOG_SERVICE_NAME
sudo systemctl restart $SERVICE_NAME $WATCHDOG_SERVICE_NAME

echo "Install complete. Generate an admin password hash and place it at $CONFIG_DIR/admin_password.hash"
