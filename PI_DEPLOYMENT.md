# Pi Deployment Guide

This guide explains how to do the first install on a Raspberry Pi and how to update the app later from GitHub.

Repository:
[https://github.com/massettc/ESSdataloggerUI.git](https://github.com/massettc/ESSdataloggerUI.git)

---

## 1. Target device assumptions

This deployment expects:

- Raspberry Pi OS or another Debian-based Linux image
- Internet access for the first download
- Git installed or installable with apt
- NetworkManager available or installable by the deployment script
- Ethernet available during first setup so the device stays reachable while Wi-Fi is configured

---

## 2. First-time install on a new Pi

### Step 1: Run the one-line installer

Paste this into a terminal on the device (curl is pre-installed on Raspberry Pi OS and most Debian images):

```bash
curl -fsSL https://raw.githubusercontent.com/massettc/ESSdataloggerUI/main/deploy/install-from-git.sh | bash -s -- https://github.com/massettc/ESSdataloggerUI.git
```

If curl is not available, use wget:

```bash
wget -qO- https://raw.githubusercontent.com/massettc/ESSdataloggerUI/main/deploy/install-from-git.sh | bash -s -- https://github.com/massettc/ESSdataloggerUI.git
```

This single command will:

- install git if not already present
- clone the repository to a temp directory
- install or enable NetworkManager if needed
- disable dhcpcd if it is still active
- tell NetworkManager to ignore Docker bridge and veth interfaces
- copy the app to /opt/pi-network-admin
- create the Python virtual environment
- install the Python dependencies
- install and start the systemd services

### Step 2: Set site-specific configuration

The installer creates `/etc/pi-network-admin/app.env` from the template on first install.
Open it and set the values that vary per device:

```bash
sudo nano /etc/pi-network-admin/app.env
```

Key values to check:

- `PI_ADMIN_WIFI_INTERFACE` — Wi-Fi interface name (default `wlan0`)
- `PI_ADMIN_ETHERNET_INTERFACE` — Ethernet interface name (default `eth0`)
- `PI_ADMIN_PRIMARY_CONNECTION_NAME` — NetworkManager connection name for Wi-Fi
- `PI_ADMIN_BACKUP_CONNECTION_NAME` — NetworkManager connection name for Ethernet
- `PI_ADMIN_PORT` — app port (default `8181`; do not use port 80)
- `PI_ADMIN_DATAPLICITY_INSTALL_URL` — your account-specific enrollment URL from the Dataplicity dashboard (e.g. `https://www.dataplicity.com/xxxxxxxx.py`). Required to use the Install Dataplicity button on the System tab.

Authentication is disabled by default — the app is open to anyone on the local network.

### Step 3: Restart the services

```bash
sudo systemctl restart pi-network-admin
sudo systemctl restart pi-network-failover
sudo systemctl restart pi-plc-alarm
```

### Step 4: Check service status

```bash
sudo systemctl status pi-network-admin
sudo systemctl status pi-network-failover
sudo systemctl status pi-plc-alarm
```

### Step 5: Open the web UI

From another device on the same network, browse to:

```text
http://<pi-ip>:8181
```

Replace the port if you changed PI_ADMIN_PORT.

---

## 3. Install a specific release version

Append the ref as a second argument to the one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/massettc/ESSdataloggerUI/main/deploy/install-from-git.sh | bash -s -- https://github.com/massettc/ESSdataloggerUI.git v0.3.4
```

Replace `v0.3.4` with any release tag.

---

## 4. Update an existing Pi install

### Update to the latest main branch

```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh main
```

If Git complains once about a dubious ownership or safe.directory check, run:

```bash
sudo git config --global --add safe.directory /opt/pi-network-admin
```

### Update to a specific tagged release

```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh v0.1.0
```

This lets you pin the Pi to a known-good version.

---

## 5. Roll back to an older release

If a newer update causes problems, switch back to the previous tag:

```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh v0.1.0
```

Replace the version with whichever tag you want to restore.

---

## 6. Typical developer workflow

When you make changes to the app on your development machine:

```bash
git add .
git commit -m "Describe the change"
git push
```

When you want a formal release:

```bash
bash deploy/create-release-tag.sh 0.1.1
```

Then update the Pi to that exact release:

```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh v0.1.1
```

---

## 7. Validation checklist after install or update

After the app is deployed, verify the following:

1. The web UI opens from the cabinet network without a login prompt.
2. The dashboard shows the Ethernet and Wi-Fi interface state.
3. Wi-Fi scan results appear.
4. A valid Wi-Fi change succeeds.
5. An invalid Wi-Fi change rolls back cleanly.
6. The Ethernet page can switch between DHCP and a static IPv4 address cleanly.
7. The failover service starts and remains active.
8. The datalogger page shows PLC connection and cloud delivery status.
9. The PLC alarm service is active (`systemctl status pi-plc-alarm`).
10. The System tab shows correct Docker and Portainer status badges.
11. The technician tools page loads and can run a quick command.
12. A reboot brings all three services back automatically.

---

## 8. Useful status commands

```bash
sudo systemctl status pi-network-admin
sudo systemctl status pi-network-failover
sudo systemctl status pi-plc-alarm
sudo journalctl -u pi-network-admin -n 100 --no-pager
sudo journalctl -u pi-network-failover -n 100 --no-pager
sudo journalctl -u pi-plc-alarm -n 100 --no-pager
nmcli device status
```

If the Pi desktop was showing repeated notifications like `You are now connected to 'veth...'`, update to the latest app build and rerun `bash deploy/install.sh` or `sudo bash deploy/update-from-git.sh main`. That installs a NetworkManager rule so Docker virtual interfaces are left unmanaged.

---

## 9. Installing infrastructure from the System tab

The System tab has one-click install buttons for Docker, Portainer, and Dataplicity. All installs run in the background and stream live output to the Technician Tools terminal.

### Docker

Click **Install Docker** on the System tab. This runs the official `get.docker.com` convenience script and adds the `pi-network-admin` user to the docker group. After install, restart the service:

```bash
sudo systemctl restart pi-network-admin
```

### Portainer

Click **Install Portainer** after Docker is installed. This pulls and starts `portainer/portainer-ce` on ports 9000/9443. The status badge updates on the next page load.

### Dataplicity

1. Log in to [dataplicity.com](https://www.dataplicity.com) and copy your device enrollment URL (format: `https://www.dataplicity.com/xxxxxxxx.py`).
2. Add it to `/etc/pi-network-admin/app.env` on the Pi:
   ```bash
   sudo nano /etc/pi-network-admin/app.env
   # Add: PI_ADMIN_DATAPLICITY_INSTALL_URL=https://www.dataplicity.com/xxxxxxxx.py
   ```
3. Restart the service: `sudo systemctl restart pi-network-admin`
4. Click **Install Dataplicity** on the System tab. Live output appears in the Technician Tools terminal.

> **Keep your enrollment URL private.** Do not commit it to a git repository. It belongs only in `app.env` on the device.

---

## 10. Notes

- The app keeps the runtime environment file in `/etc/pi-network-admin/app.env` so normal updates do not overwrite your live settings.
- Use tagged releases for field deployments when you want a stable, repeatable version.
- Keep Ethernet connected during first setup so the Pi stays reachable if Wi-Fi is not configured correctly.
