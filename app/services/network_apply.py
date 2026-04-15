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
    is_connection_active,
    is_ethernet_connected,
    is_wifi_connected,
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
        return {
            "success": False,
            "message": f"Unable to switch to {ssid}. The previous network settings were restored if available.",
        }


def apply_ethernet_settings(config: dict[str, Any], connection_name: str | None = None) -> dict[str, Any]:
    previous_connection = get_active_ethernet_connection(config)
    requested_connection = connection_name or config["ETHERNET_INTERFACE"]
    logger.info("ethernet change requested for connection=%s", requested_connection)

    try:
        if connection_name:
            bring_up_connection(config, connection_name)
        else:
            connect_device(config, config["ETHERNET_INTERFACE"])

        if _verify_connection(
            config,
            interface_name=config["ETHERNET_INTERFACE"],
            connection_type=ETHERNET_CONNECTION_TYPE,
            expected_name=connection_name,
        ):
            logger.info("ethernet change succeeded for connection=%s", requested_connection)
            return {"success": True, "message": f"Ethernet connected through {requested_connection}."}

        raise NetworkManagerError("The Ethernet connection did not become active before timeout.")
    except NetworkManagerError as exc:
        logger.warning("ethernet change failed for connection=%s: %s", requested_connection, exc)
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


def _rollback(config: dict[str, Any], previous_connection: dict[str, str] | None) -> None:
    if not previous_connection:
        logger.warning("no previous wifi connection available for rollback")
        return

    try:
        bring_up_connection(config, previous_connection["name"])
        logger.info("rolled back to previous wifi connection=%s", previous_connection["name"])
    except NetworkManagerError as exc:
        logger.error("rollback failed for connection=%s: %s", previous_connection["name"], exc)
