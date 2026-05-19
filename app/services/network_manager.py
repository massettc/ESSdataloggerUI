from __future__ import annotations

import copy
import logging
import os
import socket
import subprocess
import time
from typing import Any

_nm_logger = logging.getLogger("pi_network_admin.nmcli")


class NetworkManagerError(RuntimeError):
    pass


WIFI_CONNECTION_TYPE = "802-11-wireless"
ETHERNET_CONNECTION_TYPE = "802-3-ethernet"
_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}

# NM 1.42+ uses short type names ("ethernet", "wifi") instead of "802-3-ethernet" / "802-11-wireless"
_CONNECTION_TYPE_ALIASES: dict[str, set[str]] = {
    ETHERNET_CONNECTION_TYPE: {ETHERNET_CONNECTION_TYPE, "ethernet"},
    WIFI_CONNECTION_TYPE: {WIFI_CONNECTION_TYPE, "wifi", "wireless"},
}


def _type_matches(profile_type: str, connection_type: str) -> bool:
    aliases = _CONNECTION_TYPE_ALIASES.get(connection_type, {connection_type})
    return profile_type in aliases


def get_dashboard_state(config: dict[str, Any]) -> dict[str, Any]:
    cache_ttl = _get_cache_ttl_seconds(config, "STATUS_CACHE_SECONDS")
    cached_state = _get_cached_value(config, "dashboard_state", cache_ttl)
    if cached_state is not None:
        return cached_state

    interfaces = _get_device_status(config)
    wifi_networks = scan_wifi_networks(config)
    state = {
        "hostname": socket.gethostname(),
        "interfaces": interfaces,
        "wifi_networks": wifi_networks,
        "internet_access": has_internet_access(config),
    }
    return _set_cached_value(config, "dashboard_state", state, cache_ttl)


def scan_wifi_networks(config: dict[str, Any], force_refresh: bool = False) -> list[dict[str, Any]]:
    wifi_interface = config["WIFI_INTERFACE"]
    cache_ttl = _get_cache_ttl_seconds(config, "WIFI_SCAN_CACHE_SECONDS")
    cache_key = f"wifi_scan:{wifi_interface}"
    cached_networks = None if force_refresh else _get_cached_value(config, cache_key, cache_ttl)
    if cached_networks is not None:
        return cached_networks

    _rescan_wifi(config, wifi_interface)

    networks_by_ssid: dict[str, dict[str, Any]] = {}
    scan_commands = [
        [
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "ifname",
            wifi_interface,
            "--rescan",
            "yes",
        ],
        [
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes",
        ],
    ]

    last_error: NetworkManagerError | None = None
    for command in scan_commands:
        try:
            output = _run_nmcli(config, command)
        except NetworkManagerError as exc:
            last_error = exc
            continue

        _merge_wifi_networks(networks_by_ssid, output)
        if len(networks_by_ssid) > 1:
            break

    if not networks_by_ssid and last_error is not None:
        raise last_error

    networks = sorted(
        networks_by_ssid.values(),
        key=lambda item: (not item["in_use"], -_safe_int(item["signal"]), item["ssid"].lower()),
    )
    if not networks:
        _nm_logger.warning("WiFi scan returned no networks - scanning may be disabled or no APs available")
        return networks
    
    connected_count = sum(1 for n in networks if n.get("in_use"))
    _nm_logger.debug("WiFi scan found %d network(s), %d connected", len(networks), connected_count)
    if len(networks) <= 2 and connected_count == 1:
        _nm_logger.warning("WiFi scan returned very few networks (%d total, only 1 connected) - scanning may be partially broken", len(networks))
    
    return _set_cached_value(config, cache_key, networks, cache_ttl)


def list_connection_profiles(
    config: dict[str, Any],
    connection_type: str | None = None,
    interface_name: str | None = None,
) -> list[dict[str, str]]:
    output = _run_nmcli(config, ["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"])
    profiles = []

    for line in output.splitlines():
        parts = _split_escaped_fields(line)
        if len(parts) < 3:
            continue

        name, profile_type, device = parts[0], parts[1], ":".join(parts[2:])
        if connection_type and not _type_matches(profile_type, connection_type):
            continue

        normalized_device = "" if device == "--" else device
        if interface_name and normalized_device not in ("", interface_name):
            continue

        profiles.append(
            {
                "name": name,
                "type": profile_type,
                "device": normalized_device,
                "active": normalized_device == interface_name if interface_name else bool(normalized_device),
            }
        )

    profiles.sort(key=lambda item: (not item["active"], item["name"].lower()))
    return profiles


def get_connection_wifi_ssid(config: dict[str, Any], connection_name: str) -> str:
    """Return the SSID configured for a WiFi connection profile."""
    return _run_nmcli(config, ["-g", "802-11-wireless.ssid", "connection", "show", connection_name]).strip()


def delete_connection_profile(config: dict[str, Any], connection_name: str) -> None:
    _run_nmcli(config, ["connection", "delete", connection_name])


def find_wifi_profile_names_for_ssid(config: dict[str, Any], ssid: str) -> list[str]:
    """Return WiFi connection profile names that map to the provided SSID."""
    target_ssid = (ssid or "").strip()
    if not target_ssid:
        _nm_logger.debug("find_wifi_profile_names_for_ssid: empty SSID provided")
        return []

    # Some NetworkManager builds only allow generic fields in this listing call,
    # so fetch NAME/TYPE first and resolve SSID per profile.
    output = _run_nmcli(config, ["-t", "-f", "NAME,TYPE", "connection", "show"])
    matches: list[str] = []
    for line in output.splitlines():
        parts = _split_escaped_fields(line)
        if len(parts) < 2:
            continue

        profile_name = parts[0].strip()
        profile_type = parts[1].strip()

        if not _type_matches(profile_type, WIFI_CONNECTION_TYPE):
            continue

        if profile_name == target_ssid:
            _nm_logger.debug("found profile %s by name match for ssid=%s", profile_name, target_ssid)
            matches.append(profile_name)
            continue

        try:
            profile_ssid = get_connection_wifi_ssid(config, profile_name)
        except NetworkManagerError as exc:
            _nm_logger.debug("failed to get SSID from profile %s: %s", profile_name, exc)
            continue
        if profile_ssid == target_ssid:
            _nm_logger.debug("found profile %s by SSID match for ssid=%s", profile_name, target_ssid)
            matches.append(profile_name)

    _nm_logger.debug("find_wifi_profile_names_for_ssid: found %d profile(s) for ssid=%s: %s", len(matches), target_ssid, matches)
    return matches


def get_active_connection(
    config: dict[str, Any],
    interface_name: str,
    connection_type: str | None = None,
) -> dict[str, str] | None:
    output = _run_nmcli(
        config,
        [
            "-t",
            "-f",
            "NAME,DEVICE,TYPE",
            "connection",
            "show",
            "--active",
        ],
    )
    for line in output.splitlines():
        parts = _split_escaped_fields(line)
        if len(parts) < 3:
            continue

        name, device, active_type = parts[0], parts[1], parts[2]
        if device != interface_name:
            continue
        if connection_type and not _type_matches(active_type, connection_type):
            continue
        return {"name": name, "device": device, "type": active_type}

    return None


def get_active_wifi_connection(config: dict[str, Any]) -> dict[str, str] | None:
    return get_active_connection(config, config["WIFI_INTERFACE"], WIFI_CONNECTION_TYPE)


def get_active_ethernet_connection(config: dict[str, Any]) -> dict[str, str] | None:
    """Return the first active ethernet connection on any interface (eth0, eth1, etc.)."""
    output = _run_nmcli(config, ["-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"])
    for line in output.splitlines():
        parts = _split_escaped_fields(line)
        if len(parts) < 3:
            continue
        name, device, active_type = parts[0], parts[1], parts[2]
        if _type_matches(active_type, ETHERNET_CONNECTION_TYPE):
            return {"name": name, "device": device, "type": active_type}
    return None


def is_connection_active(
    config: dict[str, Any],
    interface_name: str,
    expected_name: str | None = None,
    connection_type: str | None = None,
) -> bool:
    active = get_active_connection(config, interface_name, connection_type=connection_type)
    if not active:
        return False
    if expected_name is None:
        return True
    return active["name"] == expected_name


def is_wifi_connected(config: dict[str, Any], expected_ssid: str | None = None) -> bool:
    return is_connection_active(
        config,
        config["WIFI_INTERFACE"],
        expected_name=expected_ssid,
        connection_type=WIFI_CONNECTION_TYPE,
    )


def is_ethernet_connected(config: dict[str, Any], expected_name: str | None = None) -> bool:
    return is_connection_active(
        config,
        config["ETHERNET_INTERFACE"],
        expected_name=expected_name,
        connection_type=ETHERNET_CONNECTION_TYPE,
    )


def set_connection_autoconnect(config: dict[str, Any], connection_name: str, enabled: bool) -> None:
    _run_nmcli(
        config,
        [
            "connection",
            "modify",
            connection_name,
            "connection.autoconnect",
            "yes" if enabled else "no",
        ],
    )


def persist_connection_to_etc(config: dict[str, Any], connection_name: str) -> None:
    """If the connection keyfile lives under /run/ (netplan-managed), copy it to
    /etc/NetworkManager/system-connections/ so it survives reboots even when
    netplan regenerates volatile profiles on every boot."""
    if os.name == "nt":
        return
    try:
        filename = _run_nmcli(config, ["-g", "filename", "connection", "show", connection_name]).strip()
    except NetworkManagerError:
        return

    if not filename or "/run/" not in filename:
        return  # Already in /etc/ or unknown — nothing to do

    dest_dir = "/etc/NetworkManager/system-connections"
    dest_file = f"{dest_dir}/{os.path.basename(filename)}"
    sudo_bin = config.get("SUDO_BIN", "sudo")
    try:
        with open(filename, "rb") as fh:
            file_content = fh.read()
        # Use tee (allowed in sudoers) instead of cp to write the keyfile
        subprocess.run(
            [sudo_bin, "-n", "tee", dest_file],
            input=file_content, check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            [sudo_bin, "-n", "chmod", "600", dest_file],
            check=True, capture_output=True, timeout=10,
        )
        # Reload so NM picks up the /etc/ copy; it will then ignore the /run/ duplicate
        _run_nmcli(config, ["connection", "reload"])
        _nm_logger.info("persisted connection %r to %s", connection_name, dest_file)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _nm_logger.warning("persist_connection_to_etc failed for %r: %s", connection_name, exc)


def bring_up_connection(config: dict[str, Any], connection_name: str, timeout_seconds: int | float | None = None) -> None:
    _run_nmcli(config, ["connection", "up", connection_name], timeout_seconds=timeout_seconds)


def force_rescan_wifi(config: dict[str, Any]) -> None:
    """Force a WiFi rescan and wait briefly for results to populate."""
    wifi_interface = config["WIFI_INTERFACE"]
    cache_key = f"wifi_scan:{wifi_interface}"
    _CACHE.pop((_get_cache_scope(config), cache_key), None)
    _rescan_wifi(config, wifi_interface)


def connect_device(config: dict[str, Any], interface_name: str) -> None:
    _run_nmcli(config, ["device", "connect", interface_name])


def set_connection_metric(config: dict[str, Any], connection_name: str, route_metric: int) -> None:
    _run_nmcli(
        config,
        [
            "connection",
            "modify",
            connection_name,
            "ipv4.route-metric",
            str(route_metric),
            "ipv6.route-metric",
            str(route_metric),
        ],
    )


def set_connection_never_default(config: dict[str, Any], connection_name: str, enabled: bool) -> None:
    _run_nmcli(
        config,
        [
            "connection",
            "modify",
            connection_name,
            "ipv4.never-default",
            "yes" if enabled else "no",
            "ipv6.never-default",
            "yes" if enabled else "no",
        ],
    )


def reapply_device(config: dict[str, Any], interface_name: str) -> None:
    _run_nmcli(config, ["device", "reapply", interface_name])


def connect_wifi(config: dict[str, Any], ssid: str, password: str, hidden: bool) -> None:
    command = [
        "device",
        "wifi",
        "connect",
        ssid,
        "ifname",
        config["WIFI_INTERFACE"],
    ]
    if password:
        command.extend(["password", password])
    if hidden:
        command.extend(["hidden", "yes"])
    _run_nmcli(config, command, timeout_seconds=config.get("WIFI_CONNECT_TIMEOUT_SECONDS"))


def get_saved_wifi_ssids(config: dict[str, Any]) -> set[str]:
    """Return a set of SSIDs that have saved WiFi profiles in NetworkManager."""
    saved_ssids = set()

    # Saved network detection should not depend on reading secrets because
    # nmcli often hides PSK values unless elevated permissions are used.
    wifi_profiles = list_connection_profiles(config, connection_type=WIFI_CONNECTION_TYPE)

    for profile in wifi_profiles:
        profile_name = profile.get("name", "").strip()
        if profile_name:
            saved_ssids.add(profile_name)

        try:
            ssid = _run_nmcli(
                config,
                ["-g", "802-11-wireless.ssid", "connection", "show", profile["name"]],
            ).strip()
            if ssid:
                saved_ssids.add(ssid)
        except NetworkManagerError:
            # Keep the profile name fallback.
            pass

    return saved_ssids


def get_saved_wifi_password_ssids(config: dict[str, Any]) -> set[str]:
    """Return SSIDs whose WiFi profiles also have a stored password/PSK."""
    saved_password_ssids = set()
    wifi_profiles = list_connection_profiles(config, connection_type=WIFI_CONNECTION_TYPE)

    for profile in wifi_profiles:
        profile_name = profile.get("name", "").strip()
        if not profile_name:
            continue

        if not _wifi_profile_has_stored_secret(config, profile_name):
            continue

        saved_password_ssids.add(profile_name)
        try:
            ssid = get_connection_wifi_ssid(config, profile_name)
            if ssid:
                saved_password_ssids.add(ssid)
        except NetworkManagerError:
            pass

    return saved_password_ssids


def _wifi_profile_has_stored_secret(config: dict[str, Any], profile_name: str) -> bool:
    try:
        flags = _run_nmcli(
            config,
            ["-g", "802-11-wireless-security.psk-flags", "connection", "show", profile_name],
        ).strip()
    except NetworkManagerError:
        return False

    if not flags or flags == "--":
        return False

    try:
        flag_value = int(flags)
    except ValueError:
        return False

    # 0 means the secret is stored normally. Other values indicate agent-owned,
    # not-saved, or not-required behavior that should not be treated as a
    # reusable saved password for this UI.
    if flag_value != 0:
        return False

    # psk-flags=0 only means "stored normally" — the actual PSK may still be
    # empty if the profile was created without a password (e.g. after a Forget).
    # Verify the PSK value is actually non-empty using sudo nmcli -s.
    try:
        sudo_bin = config.get("SUDO_BIN", "sudo")
        nmcli_bin = config.get("NMCLI_BIN", "nmcli")
        result = subprocess.run(
            [sudo_bin, "-n", nmcli_bin, "-s", "-g", "802-11-wireless-security.psk",
             "connection", "show", profile_name],
            capture_output=True,
            text=True,
            timeout=config.get("COMMAND_TIMEOUT_SECONDS", 15),
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return True  # couldn't verify the value; assume stored


def delete_saved_wifi_profiles_for_ssid(config: dict[str, Any], ssid: str) -> None:
    """Delete all WiFi connection profiles for a given SSID."""
    target_ssid = ssid.strip()
    candidate_profiles = set(find_wifi_profile_names_for_ssid(config, target_ssid))

    # Fallback to legacy matching in case profile query has missing SSID fields.
    profiles = list_connection_profiles(config, connection_type=WIFI_CONNECTION_TYPE)
    for profile in profiles:
        profile_name = profile.get("name", "").strip()
        if not profile_name:
            continue
        if profile_name == target_ssid:
            candidate_profiles.add(profile_name)
            continue
        try:
            if get_connection_wifi_ssid(config, profile_name) == target_ssid:
                candidate_profiles.add(profile_name)
        except NetworkManagerError:
            pass

    for profile_name in sorted(candidate_profiles):
        try:
            delete_connection_profile(config, profile_name)
        except NetworkManagerError as exc:
            raise NetworkManagerError(f"Failed to delete Wi-Fi profile '{profile_name}': {exc}") from exc


def get_connection_ipv4_config(config: dict[str, Any], connection_name: str) -> dict[str, str]:
    output = _run_nmcli(
        config,
        [
            "-g",
            "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns",
            "connection",
            "show",
            connection_name,
        ],
    )

    lines = output.splitlines()
    while len(lines) < 4:
        lines.append("")

    method, addresses, gateway, dns = [line.strip() for line in lines[:4]]
    address = ""
    prefix = ""

    if addresses:
        primary_address = addresses.split(",", 1)[0].strip()
        if "/" in primary_address:
            address, prefix = primary_address.split("/", 1)
        else:
            address = primary_address

    def _strip_nmcli_unset(val: str) -> str:
        return "" if val == "--" else val

    return {
        "method": method or "auto",
        "address": _strip_nmcli_unset(address),
        "prefix": _strip_nmcli_unset(prefix),
        "gateway": _strip_nmcli_unset(gateway),
        "dns": _strip_nmcli_unset(dns),
    }


def set_connection_ethernet_mac(
    config: dict[str, Any],
    connection_name: str,
    mac_address: str,
) -> None:
    """Set the cloned MAC address for an ethernet connection.

    The NM profile is set to ``ethernet.cloned-mac-address=preserve`` so that
    NetworkManager never changes or resets the MAC itself.  The actual MAC is
    applied directly to the interface via ``ip link set`` and persisted via a
    udev rule, which pre-sets it at boot before NM activates the interface.

    This pattern eliminates the carrier-flap loop that occurs when NM changes
    the MAC on an already-active link and the connected device bounces the port.
    """
    interface_name = config.get("ETHERNET_INTERFACE", "eth0")

    if os.name != "nt":
        sudo_bin = config.get("SUDO_BIN", "sudo")

        # 1. Apply the MAC to the live interface immediately.
        subprocess.run(
            [sudo_bin, "-n", "ip", "link", "set", interface_name, "address", mac_address],
            capture_output=True,
            check=False,
        )

        # 2. Persist via udev rule so the MAC is pre-set before NM activates on reboot.
        udev_path = "/etc/udev/rules.d/72-pi-network-admin-eth-mac.rules"
        rule = (
            "# Managed by pi-network-admin — do not edit by hand.\n"
            "# Pre-sets the cloned MAC address before NetworkManager activates the interface.\n"
            "# This prevents NM from changing the MAC on a live link, which would cause the\n"
            "# connected device to bounce the port (carrier-flap loop).\n"
            f'SUBSYSTEM=="net", ACTION=="add", ATTR{{interface}}=="{interface_name}",'
            f' RUN+="/usr/bin/ip link set {interface_name} address {mac_address}"\n'
        )
        try:
            subprocess.run(
                [sudo_bin, "-n", "tee", udev_path],
                input=rule,
                text=True,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                [sudo_bin, "-n", "udevadm", "control", "--reload-rules"],
                capture_output=True,
                check=False,
            )
        except subprocess.CalledProcessError:
            _nm_logger.warning("failed to write udev MAC rule for %s", interface_name)

    # 3. Set 'preserve' in the NM profile so NM never touches the MAC.
    _run_nmcli(config, [
        "connection", "modify", connection_name,
        "ethernet.cloned-mac-address", "preserve",
    ])



def set_connection_ipv4_config(
    config: dict[str, Any],
    connection_name: str,
    method: str,
    address: str = "",
    prefix: str = "",
    gateway: str = "",
    dns: str = "",
) -> None:
    normalized_method = (method or "auto").strip().lower()
    if normalized_method not in {"auto", "manual"}:
        raise NetworkManagerError("Unsupported IPv4 method. Use auto or manual.")

    if normalized_method == "manual":
        if not address:
            raise NetworkManagerError("Static IP address is required for manual Ethernet mode.")

        prefix_value = prefix or "24"

        # Step 1: set method, address, dns, and never-default (clear it unconditionally
        # so a previously-set never-default=yes doesn't silently discard the gateway)
        _run_nmcli(config, [
            "connection", "modify", connection_name,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{address}/{prefix_value}",
            "ipv4.dns", dns,
            "ipv4.never-default", "no",
            "ipv6.never-default", "no",
        ])

        # Step 2: set the gateway in its own dedicated modify call.
        # NM 1.40+ can silently drop ipv4.gateway when combined with ipv4.addresses
        # in a single modify (ordering/interaction bug observed on NM 1.52).
        _run_nmcli(config, [
            "connection", "modify", connection_name,
            "ipv4.gateway", gateway,
        ])
    else:
        _run_nmcli(config, [
            "connection", "modify", connection_name,
            "ipv4.method", "auto",
            "ipv4.addresses", "",
            "ipv4.gateway", "",
            "ipv4.dns", "",
        ])


def has_internet_access(config: dict[str, Any]) -> bool:
    target_host = config.get("WATCHDOG_TARGET_HOST", "1.1.1.1")
    cache_ttl = _get_cache_ttl_seconds(config, "STATUS_CACHE_SECONDS")
    cache_key = f"internet_access:{target_host}"
    cached_result = _get_cached_value(config, cache_key, cache_ttl)
    if cached_result is not None:
        return bool(cached_result)

    ping_bin = config.get("PING_BIN", "ping")
    count_flag = "-n" if os.name == "nt" else "-c"
    timeout_flag = "-w" if os.name == "nt" else "-W"
    timeout_value = str(max(1, int(config.get("WATCHDOG_PING_TIMEOUT_SECONDS", 2))))

    try:
        completed = subprocess.run(
            [ping_bin, count_flag, "1", timeout_flag, timeout_value, target_host],
            capture_output=True,
            text=True,
            check=False,
            timeout=max(3, int(config.get("COMMAND_TIMEOUT_SECONDS", 15))),
        )
        return _set_cached_value(config, cache_key, completed.returncode == 0, cache_ttl)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return _set_cached_value(config, cache_key, False, cache_ttl)


def _get_cache_ttl_seconds(config: dict[str, Any], setting_name: str, default: float = 0.0) -> float:
    try:
        return max(0.0, float(config.get(setting_name, default)))
    except (TypeError, ValueError):
        return default


def _get_cache_scope(config: dict[str, Any]) -> str:
    return str(config.get("REPO_PATH") or "default")


def _get_cached_value(config: dict[str, Any], cache_name: str, cache_ttl: float) -> Any | None:
    if cache_ttl <= 0:
        return None

    cache_entry = _CACHE.get((_get_cache_scope(config), cache_name))
    if cache_entry is None:
        return None

    expires_at, value = cache_entry
    if time.monotonic() >= expires_at:
        _CACHE.pop((_get_cache_scope(config), cache_name), None)
        return None

    return copy.deepcopy(value)


def _set_cached_value(config: dict[str, Any], cache_name: str, value: Any, cache_ttl: float) -> Any:
    if cache_ttl > 0:
        _CACHE[(_get_cache_scope(config), cache_name)] = (time.monotonic() + cache_ttl, copy.deepcopy(value))
    return value


def _rescan_wifi(config: dict[str, Any], wifi_interface: str) -> None:
    try:
        _run_nmcli(config, ["device", "wifi", "rescan", "ifname", wifi_interface])
        time.sleep(1)
    except NetworkManagerError:
        if os.name != "nt":
            try:
                completed = subprocess.run(
                    ["sudo", "-n", config["NMCLI_BIN"], "device", "wifi", "rescan", "ifname", wifi_interface],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=config["COMMAND_TIMEOUT_SECONDS"],
                )
                if completed.returncode == 0:
                    time.sleep(1)
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass


def _merge_wifi_networks(networks_by_ssid: dict[str, dict[str, Any]], output: str) -> None:
    for line in output.splitlines():
        if not line.strip():
            continue

        parts = _split_escaped_fields(line)
        if len(parts) < 4:
            continue

        in_use, ssid, signal, security = parts[0], parts[1], parts[2], ":".join(parts[3:])
        if not ssid:
            continue

        candidate = {
            "in_use": in_use == "*",
            "ssid": ssid,
            "signal": signal or "-",
            "security": security or "Open",
        }
        existing = networks_by_ssid.get(ssid)
        if existing is None:
            networks_by_ssid[ssid] = candidate
            continue

        if candidate["in_use"] or _safe_int(candidate["signal"]) > _safe_int(existing["signal"]):
            networks_by_ssid[ssid] = candidate


def _get_device_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    output = _run_nmcli(config, ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    interfaces = []
    for line in output.splitlines():
        parts = _split_escaped_fields(line)
        if len(parts) < 4:
            continue

        device, interface_type, state, connection = parts[0], parts[1], parts[2], ":".join(parts[3:])
        details = _get_ip_details(config, device)
        interfaces.append(
            {
                "device": device,
                "type": interface_type,
                "state": state,
                "connection": connection or "-",
                "ipv4": details["ipv4"],
                "gateway": details["gateway"],
                "dns": details["dns"],
            }
        )
    return interfaces


def _get_ip_details(config: dict[str, Any], interface_name: str) -> dict[str, str]:
    try:
        output = _run_nmcli(config, ["-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", interface_name])
    except NetworkManagerError:
        return {"ipv4": "-", "gateway": "-", "dns": "-"}

    ipv4 = []
    gateway = "-"
    dns = []
    for line in output.splitlines():
        if line.startswith("IP4.ADDRESS"):
            ipv4.append(line.split(":", 1)[1])
        elif line.startswith("IP4.GATEWAY"):
            gateway = line.split(":", 1)[1] or "-"
        elif line.startswith("IP4.DNS"):
            value = line.split(":", 1)[1]
            if value:
                dns.append(value)

    return {
        "ipv4": ", ".join(ipv4) if ipv4 else "-",
        "gateway": gateway,
        "dns": ", ".join(dns) if dns else "-",
    }


def _run_nmcli(config: dict[str, Any], arguments: list[str], timeout_seconds: int | float | None = None) -> str:
    command = _build_nmcli_command(config, arguments)
    _nm_logger.debug("nmcli cmd: %s", " ".join(str(a) for a in command))
    timeout = timeout_seconds if timeout_seconds is not None else config.get("COMMAND_TIMEOUT_SECONDS", 15)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise NetworkManagerError("nmcli is not installed on this system.") from exc
    except subprocess.TimeoutExpired as exc:
        raise NetworkManagerError("Timed out while talking to NetworkManager.") from exc
    except subprocess.CalledProcessError as exc:
        error_text = exc.stderr.strip() or exc.stdout.strip() or "Unknown NetworkManager error."
        _nm_logger.warning("nmcli failed: %s", error_text)
        raise NetworkManagerError(error_text) from exc
    _nm_logger.debug("nmcli ok: %s", completed.stdout.strip()[:200])

    return completed.stdout.strip()


def _build_nmcli_command(config: dict[str, Any], arguments: list[str]) -> list[str]:
    nmcli_bin = config.get("NMCLI_BIN", "nmcli")
    if config.get("USE_SUDO_FOR_NMCLI", False) and _is_mutating_nmcli_command(arguments):
        return ["sudo", "-n", nmcli_bin, *arguments]
    return [nmcli_bin, *arguments]


def _is_mutating_nmcli_command(arguments: list[str]) -> bool:
    mutating_prefixes = {
        ("connection", "add"),
        ("connection", "delete"),
        ("connection", "modify"),
        ("connection", "reload"),
        ("connection", "down"),
        ("connection", "up"),
        ("device", "connect"),
        ("device", "reapply"),
        ("device", "wifi", "connect"),
        ("device", "wifi", "rescan"),
    }
    for prefix in mutating_prefixes:
        if tuple(arguments[: len(prefix)]) == prefix:
            return True
    return False


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _split_escaped_fields(line: str) -> list[str]:
    parts = []
    current = []
    escaped = False

    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == ":":
            parts.append("".join(current))
            current = []
            continue

        current.append(char)

    if escaped:
        current.append("\\")

    parts.append("".join(current))
    return parts
