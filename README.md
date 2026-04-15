# ESS Datalogger UI

Local technician web UI for a Raspberry Pi datalogger. The ESSdataloggerUI app lets a technician sign in, inspect network status, manage Wi-Fi and Ethernet connections, and run a watchdog that fails traffic over between the two interfaces.

## Features

- Password-protected local admin login
- Dashboard with interface state, active connection name, IP, gateway, and DNS
- Visible Wi-Fi network scan through NetworkManager
- Wi-Fi connect flow with staged verification and rollback to the previous active wireless profile
- Ethernet profile activation and interface reconnect flow with verification and rollback
- Background watchdog service that probes upstream connectivity, promotes the configured primary interface, and fails over to the backup interface when needed
- File-based audit logging
- Deployment scaffolding for systemd and restricted sudo access to `nmcli`

## Expected target environment

- Raspberry Pi OS / Debian-based Linux
- NetworkManager enabled
- `nmcli` available on the target device
- NetworkManager connection profiles prepared for the primary and backup interfaces you want the watchdog to control

## Local development

1. Create a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Generate a password hash:
   `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('replace-me'))"`
4. Save that hash to `config/admin_password.hash` or point `PI_ADMIN_PASSWORD_HASH_FILE` at another path.
5. Export the environment variables from `config/app.env.example` as needed.
6. Set `PI_ADMIN_PRIMARY_CONNECTION_NAME` and `PI_ADMIN_BACKUP_CONNECTION_NAME` to the NetworkManager profile names you want the watchdog to promote during failover.
7. Run `python run.py` for the web UI or `python watchdog.py` to exercise the failover worker directly.

## Publish to GitHub

1. Create a new empty repository on GitHub.
2. In this project folder, run:
   - git remote add origin YOUR_GITHUB_REPO_URL
   - git commit -m "Initial ESSdataloggerUI app"
   - git push -u origin main
3. After that, the Pi can clone and install from the repo remotely.

## Deployment notes

- You can deploy from a git clone on the Pi, then run `bash deploy/install.sh` from that clone.
- For a one-step git-driven install, run `bash deploy/install-from-git.sh <repo-url> [ref]` on the Pi, where ref can be `main` or a release tag like `v0.1.0`.
- Copy `config/app.env.example` to `/etc/pi-network-admin/app.env` and set a unique secret key.
- Create `/etc/pi-network-admin/admin_password.hash` with the generated hash.
- Install the sudoers file only after reviewing the `nmcli` path on the target image.
- Review the watchdog settings, especially the primary and backup connection names, route metrics, and probe target host.
- Review `deploy/install.sh` before using it on a live image; it is a starter installer, not a finalized production script.

## Git-based deployment

1. Install git on the Pi if needed: `sudo apt-get install -y git`
2. Clone the repository on the Pi: `git clone <repo-url>`
3. Change into the clone directory.
4. Run `bash deploy/install.sh`
5. Edit `/etc/pi-network-admin/app.env`
6. Create `/etc/pi-network-admin/admin_password.hash`
7. Restart services: `sudo systemctl restart pi-network-admin pi-network-failover`

For a fresh one-step bootstrap on the Pi, you can also run:

`bash deploy/install-from-git.sh <repo-url> [ref]`

For updates after the first deploy:

1. `cd /opt/pi-network-admin`
2. `sudo bash deploy/update-from-git.sh main`

To pin the Pi to a specific release instead of the latest branch head:

1. Create and push a tag such as `v0.1.0`
2. On the Pi, run `sudo bash deploy/update-from-git.sh v0.1.0`

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

1. Log in from the cabinet LAN.
2. Confirm the dashboard shows `eth0` and `wlan0` state.
3. Open the Wi-Fi page and verify SSIDs appear.
4. Open the Ethernet page and verify saved Ethernet profiles appear.
5. Attempt a valid Wi-Fi change while Ethernet remains connected.
6. Attempt an invalid password and confirm the old Wi-Fi connection is restored.
7. Disconnect the primary link or block the watchdog target and confirm the backup profile is activated.
8. Restore the primary link and confirm the watchdog promotes it again.
9. Reboot and confirm both the web UI service and failover service start automatically.

## Next hardening steps

- Add CSRF protection.
- Add login rate limiting.
- Restrict bind/proxy exposure more tightly if needed.
- Add unit tests for more `nmcli` output variations from the target image.
