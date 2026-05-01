#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/pi-network-admin
CONFIG_DIR=/etc/pi-network-admin
HOSTNAME_HELPER_PATH=/usr/local/sbin/pi-network-admin-set-hostname
CI_CFG_DIR=/etc/cloud/cloud.cfg.d
CI_PRESERVE_HOSTNAME_CFG=99-pi-network-admin-hostname.cfg
NM_CONF_DIR=/etc/NetworkManager/conf.d
NM_DOCKER_UNMANAGED_CONF=90-pi-network-admin-unmanaged-docker.conf
SERVICE_NAME=pi-network-admin.service
WATCHDOG_SERVICE_NAME=pi-network-failover.service
PLC_ALARM_SERVICE_NAME=pi-plc-alarm.service
USER_NAME=pi-network-admin

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

# ── Preflight: ensure NetworkManager is installed ──
# Only install the package here. The dhcpcd-to-NetworkManager switch happens
# after pip install so we do not drop an active Wi-Fi connection mid-install.
if ! command -v nmcli >/dev/null 2>&1; then
    echo "NetworkManager (nmcli) not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y network-manager network-manager-gnome
fi

# Install nm-applet for desktop WiFi icon if a display is present and package is missing.
# This restores the desktop WiFi menu after switching from dhcpcd to NetworkManager.
if dpkg-query -W -f='${Status}' network-manager-gnome 2>/dev/null | grep -q "install ok installed"; then
    true  # already installed
elif [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "Installing network-manager-gnome for desktop WiFi applet..."
    sudo apt-get install -y network-manager-gnome
fi

echo "Preflight OK: NetworkManager is present ($(nmcli --version))."

# ── Preflight: ensure Python 3 venv/pip support is available ──
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "python3-venv not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-venv
fi

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    sudo useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi

if getent group docker >/dev/null 2>&1; then
    sudo usermod -aG docker "$USER_NAME"
    echo "Ensured $USER_NAME has docker group access."
else
    echo "Docker group not present yet; technician Docker commands will stay unavailable until Docker is installed."
fi

sudo mkdir -p "$APP_DIR" "$CONFIG_DIR" "$NM_CONF_DIR" /var/log/pi-network-admin
if [[ -d /etc/cloud || -d "$CI_CFG_DIR" ]]; then
    sudo mkdir -p "$CI_CFG_DIR"
fi

REPO_REALPATH=$(cd -- "$REPO_DIR" && pwd -P)
APP_REALPATH=$(cd -- "$APP_DIR" && pwd -P)

if [[ "$REPO_REALPATH" != "$APP_REALPATH" ]]; then
    sudo cp -a "$REPO_DIR"/. "$APP_DIR/"
else
    echo "Running from installed app directory; skipping source copy."
fi

sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR" /var/log/pi-network-admin

sudo -u "$USER_NAME" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER_NAME" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USER_NAME" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
if [[ ! -f "$CONFIG_DIR/app.env" ]]; then
    sudo cp "$APP_DIR/config/app.env.example" "$CONFIG_DIR/app.env"
    echo "Created $CONFIG_DIR/app.env from template."
else
    echo "Keeping existing $CONFIG_DIR/app.env"
fi
if ! sudo grep -q '^PI_ADMIN_BASH_BIN=' "$CONFIG_DIR/app.env"; then
    echo 'PI_ADMIN_BASH_BIN=/bin/bash' | sudo tee -a "$CONFIG_DIR/app.env" >/dev/null
    echo "Added PI_ADMIN_BASH_BIN to $CONFIG_DIR/app.env"
fi
if [[ ! -f "$CONFIG_DIR/plc_alarm.json" ]]; then
    sudo cp "$APP_DIR/config/plc_alarm.json" "$CONFIG_DIR/plc_alarm.json"
    echo "Created $CONFIG_DIR/plc_alarm.json from template."
else
    echo "Keeping existing $CONFIG_DIR/plc_alarm.json"
fi
if [[ ! -f "$CONFIG_DIR/technician_commands.json" ]]; then
    sudo cp "$APP_DIR/config/technician_commands.json" "$CONFIG_DIR/technician_commands.json"
    echo "Created $CONFIG_DIR/technician_commands.json from template."
else
    echo "Keeping existing $CONFIG_DIR/technician_commands.json"
fi
sudo cp "$APP_DIR/config/networkmanager-unmanaged-docker.conf" "$NM_CONF_DIR/$NM_DOCKER_UNMANAGED_CONF"
sudo cp "$APP_DIR/deploy/set-hostname.sh" "$HOSTNAME_HELPER_PATH"
sudo chmod 755 "$HOSTNAME_HELPER_PATH"
if [[ -d /etc/cloud || -d "$CI_CFG_DIR" ]]; then
    sudo cp "$APP_DIR/config/cloud-init-preserve-hostname.cfg" "$CI_CFG_DIR/$CI_PRESERVE_HOSTNAME_CFG"
fi

# ── Switch from dhcpcd to NetworkManager now that pip install is complete ──
# Doing this after pip ensures an active Wi-Fi connection is not dropped mid-install.
if systemctl is-active --quiet dhcpcd 2>/dev/null; then
    echo "Disabling dhcpcd in favour of NetworkManager..."
    sudo systemctl disable --now dhcpcd
fi
if ! systemctl is-active --quiet NetworkManager; then
    echo "Enabling NetworkManager..."
    sudo systemctl enable --now NetworkManager
fi
sudo systemctl restart NetworkManager
sudo cp "$APP_DIR/config/sudoers.pi-network-admin" /etc/sudoers.d/pi-network-admin
sudo chmod 440 /etc/sudoers.d/pi-network-admin
sudo cp "$APP_DIR/systemd/$SERVICE_NAME" /etc/systemd/system/$SERVICE_NAME
sudo cp "$APP_DIR/systemd/$WATCHDOG_SERVICE_NAME" /etc/systemd/system/$WATCHDOG_SERVICE_NAME
sudo cp "$APP_DIR/systemd/$PLC_ALARM_SERVICE_NAME" /etc/systemd/system/$PLC_ALARM_SERVICE_NAME
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME $WATCHDOG_SERVICE_NAME $PLC_ALARM_SERVICE_NAME
sudo systemctl restart $SERVICE_NAME $WATCHDOG_SERVICE_NAME $PLC_ALARM_SERVICE_NAME

# ── Post-install health check ──
sleep 3
echo ""
echo "========================================================"
echo "  Service status:"
for SVC in $SERVICE_NAME $WATCHDOG_SERVICE_NAME $PLC_ALARM_SERVICE_NAME; do
    STATE=$(systemctl is-active "$SVC" 2>/dev/null || true)
    echo "    $SVC: $STATE"
    if [[ "$STATE" != "active" ]]; then
        echo "    --- last 10 log lines ---"
        journalctl -u "$SVC" -n 10 --no-pager 2>/dev/null || true
    fi
done

APP_PORT=$(grep -m1 '^PI_ADMIN_PORT=' "$CONFIG_DIR/app.env" 2>/dev/null | cut -d= -f2 || echo "8080")
DEVICE_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<device-ip>")
echo ""
echo "  Web UI: http://${DEVICE_IP}:${APP_PORT}"
echo "========================================================"
echo ""
echo "Install complete."
