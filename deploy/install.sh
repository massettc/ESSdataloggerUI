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

# ── Preflight: ensure Python 3 venv/pip support is available ──
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "python3-venv not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-venv
fi

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    sudo useradd --system --no-create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$USER_NAME"
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
APP_REALPATH=$(sudo bash -c "cd -- '$APP_DIR' && pwd -P")

if [[ "$REPO_REALPATH" != "$APP_REALPATH" ]]; then
    sudo cp -a "$REPO_DIR"/. "$APP_DIR/"
else
    echo "Running from installed app directory; skipping source copy."
fi

sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR" /var/log/pi-network-admin

sudo -u "$USER_NAME" python3 -m venv "$APP_DIR/.venv"
# pip upgrade is best-effort; a network outage must not abort the install.
sudo -u "$USER_NAME" "$APP_DIR/.venv/bin/pip" install --upgrade pip || \
    echo "Warning: pip upgrade failed (no internet?); continuing with existing pip."
sudo -u "$USER_NAME" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" || \
    echo "Warning: pip install failed (no internet?); continuing with existing packages."
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
sudo cp "$APP_DIR/config/networkmanager-no-auto-default.conf" "$NM_CONF_DIR/91-pi-network-admin-no-auto-default.conf"
sudo cp "$APP_DIR/deploy/set-hostname.sh" "$HOSTNAME_HELPER_PATH"
sudo chmod 755 "$HOSTNAME_HELPER_PATH"
if [[ -d /etc/cloud || -d "$CI_CFG_DIR" ]]; then
    sudo cp "$APP_DIR/config/cloud-init-preserve-hostname.cfg" "$CI_CFG_DIR/$CI_PRESERVE_HOSTNAME_CFG"
fi
sudo systemctl restart NetworkManager

# ── Network: take ownership of eth0 from netplan ──────────────────────────────
# Disable cloud-init from regenerating the netplan config, remove the
# ethernets section from netplan so it never creates a competing netplan-eth0
# profile, and create a persistent NM-native profile directly in /etc/.
# After this point the app manages eth0 entirely through nmcli; no netplan
# profile will ever override it again.
_setup_ethernet_ownership() {
    local iface mac_addr np_file nm_conn_file
    iface="eth0"
    mac_addr=""

    if [[ -f "$CONFIG_DIR/app.env" ]]; then
        mac_addr=$(grep -oP '(?<=^PI_ADMIN_ETHERNET_MAC_ADDRESS=)\S+' "$CONFIG_DIR/app.env" 2>/dev/null || true)
        local custom_iface
        custom_iface=$(grep -oP '(?<=^PI_ADMIN_ETHERNET_INTERFACE=)\S+' "$CONFIG_DIR/app.env" 2>/dev/null || true)
        [[ -n "$custom_iface" ]] && iface="$custom_iface"
    fi

    nm_conn_file="/etc/NetworkManager/system-connections/${iface}.nmconnection"

    # 1. Stop cloud-init from regenerating netplan configs on future reboots.
    if [[ -d /etc/cloud ]]; then
        sudo mkdir -p "$CI_CFG_DIR"
        echo "network: {config: disabled}" | sudo tee "$CI_CFG_DIR/99-pi-network-admin-no-network.cfg" >/dev/null
        echo "Disabled cloud-init network management."
    fi

    # 2. Remove the ethernets section from the netplan YAML so netplan stops
    #    generating netplan-eth0.  WiFi config is left untouched.
    if command -v netplan >/dev/null 2>&1; then
        for np_file in /etc/netplan/*.yaml /etc/netplan/*.yml; do
            [[ -f "$np_file" ]] || continue
            if sudo grep -qE '^\s+ethernets:' "$np_file" 2>/dev/null; then
                sudo python3 - "$np_file" <<'PYEOF'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    cfg = yaml.safe_load(f) or {}
net = cfg.setdefault('network', {})
if 'ethernets' in net:
    del net['ethernets']
    with open(path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    print(f"Removed ethernets section from {path}.")
else:
    print(f"No ethernets section in {path}; nothing to remove.")
PYEOF
            fi
        done
        sudo netplan apply 2>/dev/null || true
        echo "Netplan applied; netplan-${iface} profile removed."
    fi

    # 3. Delete any lingering netplan-managed NM profile for this interface.
    if nmcli -g name connection show 2>/dev/null | grep -q "^netplan-${iface}$"; then
        sudo nmcli connection delete "netplan-${iface}" 2>/dev/null || true
        echo "Deleted lingering netplan-${iface} NM profile."
    fi

    # 3b. Delete NM-auto-generated default ethernet profiles ("Ethernet connection N",
    #     "Wired Connection N") that have no interface binding.  These are created by
    #     NM on first boot before no-auto-default is deployed.  They confuse NM when
    #     eth0 has NO-CARRIER because NM may try to activate them instead of our
    #     canonical profile.
    while IFS= read -r puuid; do
        [[ -z "$puuid" ]] && continue
        local auto_name auto_iface
        auto_name=$(sudo nmcli -g connection.id connection show "$puuid" 2>/dev/null | tr -d '\r\n')
        auto_iface=$(sudo nmcli -g connection.interface-name connection show "$puuid" 2>/dev/null | tr -d ' \r\n')
        # Match "Ethernet connection N" / "Wired Connection N" with no interface binding.
        if [[ -z "$auto_iface" || "$auto_iface" == "--" ]] && \
           [[ "$auto_name" =~ ^[Ee]thernet[[:space:]][Cc]onnection[[:space:]][0-9]+$ ]] || \
           [[ -z "$auto_iface" || "$auto_iface" == "--" ]] && \
           [[ "$auto_name" =~ ^[Ww]ired[[:space:]][Cc]onnection[[:space:]][0-9]+$ ]]; then
            sudo nmcli connection delete "$puuid" 2>/dev/null || true
            echo "Deleted NM auto-default profile: '${auto_name}'."
        fi
    done < <(sudo nmcli -t -f uuid,type connection show 2>/dev/null | \
        awk -F: '($2 == "ethernet" || $2 == "802-3-ethernet") {print $1}')

    # 4+5. Ensure exactly one NM profile for this interface with the correct MAC.
    #
    #   Count profiles whose con-name == iface OR connection.interface-name == iface.
    #   • 0 profiles  → create one fresh.
    #   • 1 profile   → just update the MAC; don't touch anything else so saved
    #                   gateway/IP settings are preserved.
    #   • 2+ profiles → nuclear wipe and create one fresh (duplicates break NM).
    #
    #   This makes the function safe to run on every update without accumulating
    #   extra profiles.
    #
    #   Reload NM's keyfile cache first so that profiles saved by the web UI
    #   (or written externally) are visible before we count.  Without this, NM
    #   may not yet have loaded a freshly-written keyfile and we'd incorrectly
    #   enter the 0-profile branch, replacing a static-IP profile with DHCP.
    sudo nmcli connection reload 2>/dev/null || true

    local eth_uuids=()
    while IFS= read -r puuid; do
        [[ -z "$puuid" ]] && continue
        local pcon_name piface_name
        pcon_name=$(sudo nmcli -g connection.id            connection show "$puuid" 2>/dev/null | tr -d '\r\n')
        piface_name=$(sudo nmcli -g connection.interface-name connection show "$puuid" 2>/dev/null | tr -d ' \r\n')
        if [[ "$pcon_name" == "$iface" || "$piface_name" == "$iface" ]]; then
            eth_uuids+=("$puuid")
        fi
    done < <(sudo nmcli -t -f uuid,type connection show 2>/dev/null | \
        awk -F: '($2 == "ethernet" || $2 == "802-3-ethernet") {print $1}')

    local profile_count=${#eth_uuids[@]}

    if [[ $profile_count -eq 1 ]]; then
        # Happy path: exactly one profile.
        # Set connection.id = iface so NM writes the keyfile as <iface>.nmconnection.
        # Without this the file keeps its old name (e.g. 'Wired Connection 1.nmconnection')
        # and the watchdog can't find the canonical path, causing it to create a
        # duplicate on every boot.
        local mod_args=(connection modify "${eth_uuids[0]}"
            connection.id               "$iface"
            connection.interface-name   "$iface"
            connection.autoconnect      yes
            connection.autoconnect-retries 0)
        [[ -n "$mac_addr" ]] && mod_args+=(ethernet.cloned-mac-address "$mac_addr")
        sudo nmcli "${mod_args[@]}" 2>/dev/null || true
        echo "One '${iface}' profile found; normalised name/MAC (uuid=${eth_uuids[0]})." 

    elif [[ $profile_count -eq 0 ]]; then
        # No profile at all — create one.
        local -a add_args=(connection add type ethernet ifname "$iface" con-name "$iface"
            connection.autoconnect yes connection.autoconnect-retries 0
            ipv4.method auto)
        [[ -n "$mac_addr" ]] && add_args+=(ethernet.cloned-mac-address "$mac_addr")
        sudo nmcli "${add_args[@]}"
        echo "No '${iface}' profile found; created one."

    else
        # Multiple profiles — wipe them all and start clean.
        echo "Found ${profile_count} profiles for '${iface}'; performing cleanup."
        for puuid in "${eth_uuids[@]}"; do
            sudo nmcli connection delete "$puuid" 2>/dev/null || true
        done
        # Remove any orphaned keyfiles.
        sudo find /etc/NetworkManager/system-connections \
            -maxdepth 1 -name "${iface}*.nmconnection" -delete 2>/dev/null || true
        # Create fresh profile BEFORE reloading so NM never sees a gap.
        local -a add_args=(connection add type ethernet ifname "$iface" con-name "$iface"
            connection.autoconnect yes connection.autoconnect-retries 0
            ipv4.method auto)
        [[ -n "$mac_addr" ]] && add_args+=(ethernet.cloned-mac-address "$mac_addr")
        sudo nmcli "${add_args[@]}"
        sudo nmcli connection reload 2>/dev/null || true
        echo "Recreated single '${iface}' profile."
    fi
}
_setup_ethernet_ownership


sudo chmod 440 /etc/sudoers.d/pi-network-admin
sudo cp "$APP_DIR/systemd/$SERVICE_NAME" /etc/systemd/system/$SERVICE_NAME
sudo cp "$APP_DIR/systemd/$WATCHDOG_SERVICE_NAME" /etc/systemd/system/$WATCHDOG_SERVICE_NAME
sudo cp "$APP_DIR/systemd/$PLC_ALARM_SERVICE_NAME" /etc/systemd/system/$PLC_ALARM_SERVICE_NAME
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME $WATCHDOG_SERVICE_NAME $PLC_ALARM_SERVICE_NAME
sudo systemctl restart $SERVICE_NAME $WATCHDOG_SERVICE_NAME $PLC_ALARM_SERVICE_NAME

echo "Install complete. Generate an admin password hash and place it at $CONFIG_DIR/admin_password.hash"
