from __future__ import annotations

import logging
import time
from typing import Any

from app.services.network_manager import (
    delete_connection_profile,
    ETHERNET_CONNECTION_TYPE,
    find_wifi_profile_names_for_ssid,
    WIFI_CONNECTION_TYPE,
    NetworkManagerError,
    bring_up_connection,
    connect_device,
    connect_wifi,
    get_active_connection,
    get_active_ethernet_connection,
    get_active_wifi_connection,
    get_connection_ipv4_config,
    get_connection_wifi_ssid,
    is_connection_active,
    is_ethernet_connected,
    is_wifi_connected,
    list_connection_profiles,
    persist_connection_to_etc,
    set_connection_autoconnect,
    set_connection_ipv4_config,
    set_connection_never_default,
)


logger = logging.getLogger("pi_network_admin")


def apply_wifi_settings(config: dict[str, Any], ssid: str, password: str, hidden: bool) -> dict[str, Any]:
    previous_connection = get_active_wifi_connection(config)
    logger.info("wifi change requested for ssid=%s hidden=%s", ssid, hidden)

    try:
        _connect_wifi_with_profile_recovery(config, ssid=ssid, password=password, hidden=hidden)
        logger.info("wifi change succeeded for ssid=%s", ssid)
        return {"success": True, "message": f"Connected to {ssid}."}
    except NetworkManagerError as exc:
        logger.warning("wifi change failed for ssid=%s: %s", ssid, exc)
        _rollback(config, previous_connection)
        detail = str(exc).strip()
        return {
            "success": False,
            "message": f"Unable to switch to {ssid}. {detail} Previous network settings were restored if available.",
        }


def _connect_wifi_with_profile_recovery(config: dict[str, Any], ssid: str, password: str, hidden: bool) -> None:
    try:
        connect_wifi(config, ssid=ssid, password=password, hidden=hidden)
    except NetworkManagerError as exc:
        if not _is_missing_key_mgmt_error(exc):
            raise

        if not password:
            raise NetworkManagerError(
                f"Saved Wi-Fi profile for {ssid} is invalid. Enter the Wi-Fi password and try again."
            ) from exc

        logger.warning("detected invalid key-mgmt profile for ssid=%s, removing stale profile(s) and retrying", ssid)
        _delete_wifi_profiles_for_ssid(config, ssid)
        try:
            connect_wifi(config, ssid=ssid, password=password, hidden=hidden)
        except NetworkManagerError as retry_exc:
            if not _is_missing_key_mgmt_error(retry_exc):
                raise
            logger.warning("retry still failing with key-mgmt for ssid=%s, forcing clean profile rebuild", ssid)
            _rebuild_wifi_profile_and_connect(config, ssid=ssid, password=password, hidden=hidden)

    if _verify_wifi_connection(config, ssid):
        return

    raise NetworkManagerError("The new Wi-Fi connection did not become active before timeout.")


def _is_missing_key_mgmt_error(exc: NetworkManagerError) -> bool:
    return "802-11-wireless-security.key-mgmt" in str(exc)


def _delete_wifi_profiles_for_ssid(config: dict[str, Any], ssid: str) -> None:
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
        except NetworkManagerError as delete_exc:
            logger.warning("failed to delete stale wifi profile=%s for ssid=%s: %s", profile_name, target_ssid, delete_exc)


def _rebuild_wifi_profile_and_connect(config: dict[str, Any], ssid: str, password: str, hidden: bool) -> None:
    if not password:
        raise NetworkManagerError(f"Cannot rebuild Wi-Fi profile for {ssid} without a password.")

    _delete_wifi_profiles_for_ssid(config, ssid)
    interface = config["WIFI_INTERFACE"]

    # Build a clean profile with explicit key-mgmt so NetworkManager cannot inherit a broken security block.
    from app.services.network_manager import _run_nmcli  # local import to avoid widening public API

    _run_nmcli(
        config,
        [
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            interface,
            "con-name",
            ssid,
            "ssid",
            ssid,
        ],
    )
    _run_nmcli(
        config,
        [
            "connection",
            "modify",
            ssid,
            "802-11-wireless-security.key-mgmt",
            "wpa-psk",
            "802-11-wireless-security.psk",
            password,
            "802-11-wireless.hidden",
            "yes" if hidden else "no",
            "connection.autoconnect",
            "yes",
        ],
    )
    bring_up_connection(config, ssid)


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
    config_saved = False
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
            set_connection_autoconnect(config, target_connection, True)
            persist_connection_to_etc(config, target_connection)
            config_saved = True

        # When a manual gateway is explicitly provided, keep ethernet eligible for
        # default route so NetworkManager does not discard the gateway.
        prefer_wlan = config.get("PREFER_WLAN_FOR_INTERNET", False)
        should_mark_non_default = prefer_wlan and not (ip_method == "manual" and gateway)
        if target_connection and should_mark_non_default:
            set_connection_never_default(config, target_connection, True)

        try:
            if target_connection:
                bring_up_connection(config, target_connection)
            else:
                connect_device(config, config["ETHERNET_INTERFACE"])
        except NetworkManagerError as activate_exc:
            logger.warning(
                "ethernet activation failed for connection=%s (settings were saved): %s",
                requested_connection,
                activate_exc,
            )
            return {
                "success": True,
                "message": (
                    f"Settings saved for {requested_connection}. "
                    "The connection could not be activated — the device may be unavailable or no cable is connected. "
                    "Settings will apply automatically when the cable is plugged in."
                ),
            }

        if _verify_connection(
            config,
            interface_name=None,
            connection_type=ETHERNET_CONNECTION_TYPE,
            expected_name=target_connection,
        ):
            logger.info("ethernet change succeeded for connection=%s", requested_connection)
            return {"success": True, "message": f"Ethernet connected through {requested_connection}."}

        raise NetworkManagerError("The Ethernet connection did not become active before timeout.")
    except NetworkManagerError as exc:
        logger.warning("ethernet change failed for connection=%s: %s", requested_connection, exc)
        if not config_saved and target_connection and previous_ipv4_config is not None:
            _restore_ipv4_config(config, target_connection, previous_ipv4_config)
        _rollback(config, previous_connection)
        if config_saved:
            return {
                "success": True,
                "message": (
                    f"Settings saved for {requested_connection}. "
                    "The connection could not be verified as active — check that the cable is connected. "
                    "Settings are stored and will apply when the connection comes up."
                ),
            }
        return {
            "success": False,
            "message": f"Unable to activate Ethernet connection {requested_connection}. The previous Ethernet profile was restored if available.",
        }


def _verify_wifi_connection(config: dict[str, Any], expected_ssid: str) -> bool:
    # Check that wlan0 has *any* active WiFi connection — not by profile name, which
    # may differ from the SSID (e.g. NM may store the profile as "ESS 1" while the
    # SSID is "ESS").  nmcli device wifi connect already confirmed the SSID, so we
    # just need to confirm the interface is up.
    return _verify_connection(
        config,
        interface_name=config["WIFI_INTERFACE"],
        connection_type=WIFI_CONNECTION_TYPE,
        expected_name=None,
    )


def _verify_connection(
    config: dict[str, Any],
    interface_name: str | None,
    connection_type: str,
    expected_name: str | None = None,
) -> bool:
    deadline = time.monotonic() + config["VERIFY_TIMEOUT_SECONDS"]
    while time.monotonic() < deadline:
        if interface_name is None and connection_type == ETHERNET_CONNECTION_TYPE:
            # Any ethernet interface — used when profile may roam between eth0/eth1
            active = get_active_ethernet_connection(config)
            if active and (expected_name is None or active["name"] == expected_name):
                return True
        elif interface_name and is_connection_active(
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
