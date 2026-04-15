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

### Step 1: Install git if needed

```bash
sudo apt-get update
sudo apt-get install -y git
```

### Step 2: Download the project from GitHub

```bash
cd ~
git clone https://github.com/massettc/ESSdataloggerUI.git
cd ESSdataloggerUI
```

### Step 3: Run the installer

```bash
bash deploy/install.sh
```

The installer will:

- install or enable NetworkManager if needed
- disable dhcpcd if it is still active
- copy the app to /opt/pi-network-admin
- create the Python virtual environment
- install the Python dependencies
- install the systemd services

### Step 4: Create the runtime configuration

Edit the runtime environment file:

```bash
sudo nano /etc/pi-network-admin/app.env
```

Set the important values:

- PI_ADMIN_SECRET_KEY
- PI_ADMIN_PORT
- PI_ADMIN_WIFI_INTERFACE
- PI_ADMIN_ETHERNET_INTERFACE
- PI_ADMIN_PRIMARY_CONNECTION_NAME
- PI_ADMIN_BACKUP_CONNECTION_NAME

### Step 5: Create the admin password hash

Generate a password hash:

```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('ChangeThisPassword'))"
```

Copy the output and save it here:

```bash
sudo nano /etc/pi-network-admin/admin_password.hash
```

Paste the hash on a single line and save.

### Step 6: Restart the services

```bash
sudo systemctl restart pi-network-admin
sudo systemctl restart pi-network-failover
```

### Step 7: Check service status

```bash
sudo systemctl status pi-network-admin
sudo systemctl status pi-network-failover
```

### Step 8: Open the web UI

From another device on the same network, browse to:

```text
http://<pi-ip>:8080
```

Replace the port if you changed PI_ADMIN_PORT.

---

## 3. Install a specific release version

If you want the Pi to run a specific tagged release instead of the latest code on main:

```bash
cd ~
git clone https://github.com/massettc/ESSdataloggerUI.git
cd ESSdataloggerUI
git checkout v0.1.0
bash deploy/install.sh
```

You can replace v0.1.0 with any later release tag.

---

## 4. Update an existing Pi install

### Update to the latest main branch

```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh main
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

1. The web UI opens from the cabinet network.
2. Login works with the configured admin password.
3. The dashboard shows the Ethernet and Wi-Fi interface state.
4. Wi-Fi scan results appear.
5. A valid Wi-Fi change succeeds.
6. An invalid Wi-Fi change rolls back cleanly.
7. The failover service starts and remains active.
8. A reboot brings both services back automatically.

---

## 8. Useful status commands

```bash
sudo systemctl status pi-network-admin
sudo systemctl status pi-network-failover
sudo journalctl -u pi-network-admin -n 100 --no-pager
sudo journalctl -u pi-network-failover -n 100 --no-pager
nmcli device status
```

---

## 9. Notes

- The app keeps the runtime environment file in /etc/pi-network-admin/app.env so normal updates do not overwrite your live settings.
- Use tagged releases for field deployments when you want a stable, repeatable version.
- Keep Ethernet connected during first setup so the Pi stays reachable if Wi-Fi is not configured correctly.
