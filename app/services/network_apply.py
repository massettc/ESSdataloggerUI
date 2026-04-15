from __future__ import annotations

import logging
import time
from typing import Any

from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    WIFI_CONNECTION_TYPE,
    NetworkManagerError,
    bring_up_connection,
    connect_device,
    connect_wifi,
    get_active_connection,
    get_active_ethernet_connection,
    get_active_wifi_connection,
    get_connection_ipv4_config,
    is_connection_active,
    is_ethernet_connected,
    is_wifi_connected,
    set_connection_ipv4_config,
)


logger = logging.getLogger("pi_network_admin")


def apply_wifi_settings(config: dict[str, Any], ssid: str, password: str, hidden: bool) -> dict[str, Any]:
    previous_connection = get_active_wifi_connection(config)
    logger.info("wifi change requested for ssid=%s hidden=%s", ssid, hidden)

    try:
        connect_wifi(config, ssid=ssid, password=password, hidden=hidden)
        if _verify_wifi_connection(config, ssid):
            logger.info("wifi change succeeded for ssid=%s", ssid)
            return {"success": True, "message": f"Connected to {ssid}."}

        raise NetworkManagerError("The new Wi-Fi connection did not become active before timeout.")
    except NetworkManagerError as exc:
        logger.warning("wifi change failed for ssid=%s: %s", ssid, exc)
        _rollback(config, previous_connection)
        detail = str(exc).strip()
        return {
            "success": False,
            "message": f"Unable to switch to {ssid}. {detail} Previous network settings were restored if available.",
        }


def apply_ethernet_settings(
    config: dict[str, Any],
    connection_name: str | None = None,
    ip_method: str | None = None,
    ip_address: str = "",
    ip_prefix: str = "",
    gateway: str = "",
    dns: str = "",
) -> dict[str, Any]:
    previous_connection = get_active_ethernet_connection(config)
    target_connection = connection_name
    if target_connection is None and ip_method in {"auto", "manual"} and previous_connection:
        target_connection = previous_connection["name"]

    requested_connection = target_connection or config["ETHERNET_INTERFACE"]
    previous_ipv4_config = None
    logger.info("ethernet change requested for connection=%s method=%s", requested_connection, ip_method or "unchanged")

    if ip_method == "manual" and not ip_address:
        raise NetworkManagerError("Static IP address is required when Ethernet mode is set to static.")

    try:
        if target_connection and ip_method in {"auto", "manual"}:
            previous_ipv4_config = get_connection_ipv4_config(config, target_connection)
            set_connection_ipv4_config(
                config,
                connection_name=target_connection,
                method=ip_method,
                address=ip_address,
                prefix=ip_prefix,
                gateway=gateway,
                dns=dns,
            )

        if target_connection:
            bring_up_connection(config, target_connection)
        else:
            connect_device(config, config["ETHERNET_INTERFACE"])

        if _verify_connection(
            config,
            interface_name=config["ETHERNET_INTERFACE"],
            connection_type=ETHERNET_CONNECTION_TYPE,
            expected_name=target_connection,
        ):
            logger.info("ethernet change succeeded for connection=%s", requested_connection)
            return {"success": True, "message": f"Ethernet connected through {requested_connection}."}

        raise NetworkManagerError("The Ethernet connection did not become active before timeout.")
    except NetworkManagerError as exc:
        logger.warning("ethernet change failed for connection=%s: %s", requested_connection, exc)
        if target_connection and previous_ipv4_config is not None:
            _restore_ipv4_config(config, target_connection, previous_ipv4_config)
        _rollback(config, previous_connection)
        return {
            "success": False,
            "message": f"Unable to activate Ethernet connection {requested_connection}. The previous Ethernet profile was restored if available.",
        }


def _verify_wifi_connection(config: dict[str, Any], expected_ssid: str) -> bool:
    return _verify_connection(
        config,
        interface_name=config["WIFI_INTERFACE"],
        connection_type=WIFI_CONNECTION_TYPE,
        expected_name=expected_ssid,
    )


def _verify_connection(
    config: dict[str, Any],
    interface_name: str,
    connection_type: str,
    expected_name: str | None = None,
) -> bool:
    deadline = time.monotonic() + config["VERIFY_TIMEOUT_SECONDS"]
    while time.monotonic() < deadline:
        if is_connection_active(
            config,
            interface_name,
            expected_name=expected_name,
            connection_type=connection_type,
        ):
            return True
        time.sleep(config["VERIFY_POLL_SECONDS"])
    return False


def ensure_connection_active(config: dict[str, Any], interface_name: str, connection_name: str | None = None) -> bool:
    if interface_name == config["WIFI_INTERFACE"]:
        return is_wifi_connected(config, expected_ssid=connection_name)
    if interface_name == config["ETHERNET_INTERFACE"]:
        return is_ethernet_connected(config, expected_name=connection_name)

    active = get_active_connection(config, interface_name)
    if not active:
        return False
    if connection_name is None:
        return True
    return active["name"] == connection_name


def _restore_ipv4_config(config: dict[str, Any], connection_name: str, previous_ipv4_config: dict[str, str]) -> None:
    try:
        set_connection_ipv4_config(
            config,
            connection_name=connection_name,
            method=previous_ipv4_config.get("method", "auto"),
            address=previous_ipv4_config.get("address", ""),
            prefix=previous_ipv4_config.get("prefix", ""),
            gateway=previous_ipv4_config.get("gateway", ""),
            dns=previous_ipv4_config.get("dns", ""),
        )
        logger.info("restored IPv4 settings for connection=%s", connection_name)
    except NetworkManagerError as exc:
        logger.error("failed to restore IPv4 settings for connection=%s: %s", connection_name, exc)


def _rollback(config: dict[str, Any], previous_connection: dict[str, str] | None) -> None:
    if not previous_connection:
        logger.warning("no previous connection available for rollback")
        return

    try:
        bring_up_connection(config, previous_connection["name"])
        logger.info("rolled back to previous connection=%s", previous_connection["name"])
    except NetworkManagerError as exc:
        logger.error("rollback failed for connection=%s: %s", previous_connection["name"], exc)
