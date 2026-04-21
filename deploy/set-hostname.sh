#!/usr/bin/env bash
set -euo pipefail

NEW_HOSTNAME=${1:-}

if [[ -z "$NEW_HOSTNAME" ]]; then
    echo "hostname is required" >&2
    exit 1
fi

if [[ ! "$NEW_HOSTNAME" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]; then
    echo "invalid hostname" >&2
    exit 1
fi

hostnamectl set-hostname "$NEW_HOSTNAME"
printf '%s\n' "$NEW_HOSTNAME" > /etc/hostname

hosts_tmp=$(mktemp)
awk -v hostname="$NEW_HOSTNAME" '
BEGIN { updated = 0 }
/^[[:space:]]*127\.0\.1\.1[[:space:]]+/ {
    if (!updated) {
        print "127.0.1.1\t" hostname
        updated = 1
    }
    next
}
{ print }
END {
    if (!updated) {
        print "127.0.1.1\t" hostname
    }
}
' /etc/hosts > "$hosts_tmp"
cat "$hosts_tmp" > /etc/hosts
rm -f "$hosts_tmp"