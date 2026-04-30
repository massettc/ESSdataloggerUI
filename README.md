# ESS Datalogger UI

Local technician web UI for a Raspberry Pi datalogger. The ESSdataloggerUI app lets a technician inspect network status, manage Wi-Fi and Ethernet connections, run a watchdog that fails traffic over between the two interfaces, and install supporting software like Docker, Portainer, and Dataplicity directly from the web UI.

## Features

- Dashboard with interface state, active connection name, IP, gateway, and DNS
- Wi-Fi workflow with scan, select, password entry, connect, verification, and rollback to previous profile
- Ethernet profile selection plus DHCP or static IPv4 configuration with verification and rollback
- Background watchdog service that probes upstream connectivity, promotes the configured primary interface, and fails over to the backup interface when needed
- Optional PLC alarm worker that writes a Modbus holding register when cloud delivery is unhealthy for a configurable duration
- System tab: hostname management, disk usage, git-based app updates, one-click Docker install, one-click Portainer install/start, one-click Dataplicity install
- Technician Tools tab: live terminal output for saved and custom commands
- File-based audit logging
- Deployment scaffolding for systemd and restricted sudo access to `nmcli`
- Authentication disabled by default (open LAN access) — enable with `PI_ADMIN_AUTH_ENABLED=true`

## Expected target environment

- Raspberry Pi OS / Debian-based Linux
- NetworkManager enabled
- `nmcli` available on the target device
- NetworkManager connection profiles prepared for the primary and backup interfaces you want the watchdog to control

## Local development

1. Create a virtual environment and install dependencies from `requirements.txt`.
2. Export the environment variables from `config/app.env.example` as needed.
3. Set `PI_ADMIN_PRIMARY_CONNECTION_NAME` and `PI_ADMIN_BACKUP_CONNECTION_NAME` to the NetworkManager profile names you want the watchdog to promote during failover.
4. Run `python run.py` for the web UI or `python watchdog.py` to exercise the failover worker directly.

Authentication is disabled by default (`AUTH_ENABLED=false`). To enable it, set `PI_ADMIN_AUTH_ENABLED=true` and create a password hash file:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('replace-me'))" > config/admin_password.hash
```

## Publish to GitHub

1. Create a new empty repository on GitHub.
2. In this project folder, run:
   - `git remote add origin YOUR_GITHUB_REPO_URL`
   - `git commit -m "Initial ESSdataloggerUI app"`
   - `git push -u origin main`
3. After that, the Pi can clone and install from the repo remotely.

> **Security note:** Do not commit real secrets to the repo. The `config/admin_password.hash` and `config/app.env` files are gitignored. All sensitive values — including `PI_ADMIN_SECRET_KEY` and `PI_ADMIN_DATAPLICITY_INSTALL_URL` — live only in `/etc/pi-network-admin/app.env` on the device.

## Deployment notes

- Run `bash deploy/install-from-git.sh <repo-url>` on the Pi for a one-step install.
- Copy `config/app.env.example` to `/etc/pi-network-admin/app.env` and update site-specific values. The installer does this automatically on first install.
- Key values to set per device: `PI_ADMIN_PRIMARY_CONNECTION_NAME`, `PI_ADMIN_BACKUP_CONNECTION_NAME`, `PI_ADMIN_SECRET_KEY`, `PI_ADMIN_DATAPLICITY_INSTALL_URL`.
- Install the sudoers file only after reviewing the binary paths on the target image.
- Review `deploy/install.sh` before using it on a live image.

## Git-based deployment

Fresh install:
```bash
curl -fsSL https://raw.githubusercontent.com/massettc/ESSdataloggerUI/main/deploy/install-from-git.sh | bash -s -- https://github.com/massettc/ESSdataloggerUI.git
```

Update existing install:
```bash
cd /opt/pi-network-admin
sudo bash deploy/update-from-git.sh main
```

Pin to a specific release:
```bash
sudo bash deploy/update-from-git.sh v0.3.4
```

## Versioned release workflow

1. Make and test your changes locally.
2. Commit and push them to GitHub.
3. When you want a deployable milestone, create and push an annotated release tag such as `v0.1.0`:
   - manual: `git tag -a v0.1.0 -m "Release v0.1.0" && git push origin v0.1.0`
   - helper script: `bash deploy/create-release-tag.sh 0.1.0`
4. Install or update the Pi to that exact version:
   - fresh install: `bash deploy/install-from-git.sh <repo-url> v0.1.0`
   - existing install: `sudo bash deploy/update-from-git.sh v0.1.0`
5. If you want the newest development state instead, use `main` as the ref.

The current repository version marker is stored in `VERSION`.

## Validation checklist

1. Log in from the cabinet LAN (or confirm UI is accessible if auth is disabled).
2. Confirm the dashboard shows `eth0` and `wlan0` state.
3. Open the Wi-Fi page and verify SSIDs appear.
4. Open the Ethernet page and verify saved Ethernet profiles appear.
5. Attempt a valid Wi-Fi change while Ethernet remains connected.
6. Attempt an invalid password and confirm the old Wi-Fi connection is restored.
7. Disconnect the primary link or block the watchdog target and confirm the backup profile is activated.
8. Restore the primary link and confirm the watchdog promotes it again.
9. Open the System tab and verify Docker and Portainer status badges reflect the real device state.
10. Reboot and confirm all three services (`pi-network-admin`, `pi-network-failover`, `pi-plc-alarm`) start automatically.
