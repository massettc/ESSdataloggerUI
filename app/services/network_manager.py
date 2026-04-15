from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Any


class NetworkManagerError(RuntimeError):
    pass


WIFI_CONNECTION_TYPE = "802-11-wireless"
ETHERNET_CONNECTION_TYPE = "802-3-ethernet"


def get_dashboard_state(config: dict[str, Any]) -> dict[str, Any]:
    interfaces = _get_device_status(config)
    wifi_networks = scan_wifi_networks(config)
    return {
        "hostname": socket.gethostname(),
        "interfaces": interfaces,
        "wifi_networks": wifi_networks,
    }


def scan_wifi_networks(config: dict[str, Any]) -> list[dict[str, Any]]:
    wifi_interface = config["WIFI_INTERFACE"]
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

    return sorted(
        networks_by_ssid.values(),
        key=lambda item: (not item["in_use"], -_safe_int(item["signal"]), item["ssid"].lower()),
    )


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
        if connection_type and profile_type != connection_type:
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
        if connection_type and active_type != connection_type:
            continue
        return {"name": name, "device": device, "type": active_type}

    return None


def get_active_wifi_connection(config: dict[str, Any]) -> dict[str, str] | None:
    return get_active_connection(config, config["WIFI_INTERFACE"], WIFI_CONNECTION_TYPE)


def get_active_ethernet_connection(config: dict[str, Any]) -> dict[str, str] | None:
    return get_active_connection(config, config["ETHERNET_INTERFACE"], ETHERNET_CONNECTION_TYPE)


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


def bring_up_connection(config: dict[str, Any], connection_name: str) -> None:
    _run_nmcli(config, ["connection", "up", connection_name])


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
    _run_nmcli(config, command)


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

    return {
        "method": method or "auto",
        "address": address,
        "prefix": prefix,
        "gateway": gateway,
        "dns": dns,
    }


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

    command = ["connection", "modify", connection_name, "ipv4.method", normalized_method]

    if normalized_method == "manual":
        if not address:
            raise NetworkManagerError("Static IP address is required for manual Ethernet mode.")

        prefix_value = prefix or "24"
        command.extend(
            [
                "ipv4.addresses",
                f"{address}/{prefix_value}",
                "ipv4.gateway",
                gateway,
                "ipv4.dns",
                dns,
            ]
        )
    else:
        command.extend(["ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""])

    _run_nmcli(config, command)


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


def _run_nmcli(config: dict[str, Any], arguments: list[str]) -> str:
    command = [config["NMCLI_BIN"], *arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=config["COMMAND_TIMEOUT_SECONDS"],
        )
    except FileNotFoundError as exc:
        raise NetworkManagerError("nmcli is not installed on this system.") from exc
    except subprocess.TimeoutExpired as exc:
        raise NetworkManagerError("Timed out while talking to NetworkManager.") from exc
    except subprocess.CalledProcessError as exc:
        error_text = exc.stderr.strip() or exc.stdout.strip() or "Unknown NetworkManager error."
        raise NetworkManagerError(error_text) from exc

    return completed.stdout.strip()


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
