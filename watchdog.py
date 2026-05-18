import logging

from app import create_app
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    list_connection_profiles,
    replace_netplan_ethernet_profile,
)
from app.services.network_watchdog import FailoverWatchdog


app = create_app()


def _enforce_ethernet_mac() -> None:
    """Replace any netplan-managed ethernet profiles with persistent NM-native ones.

    netplan regenerates profiles in /run/NetworkManager/system-connections/ on
    every boot, wiping any runtime changes.  replace_netplan_ethernet_profile
    creates a new keyfile in /etc/ (which netplan never touches) with the pinned
    MAC address and the same IP settings, then removes the old volatile profile.
    If the profile is already in /etc/ it just updates the MAC in-place.

    Called once here in the watchdog process so only one process modifies NM
    profiles at startup.
    """
    config = app.config
    mac_address = config.get("ETHERNET_MAC_ADDRESS", "")
    if not mac_address:
        return

    logger = logging.getLogger("pi_network_admin")
    try:
        profiles = list_connection_profiles(config, connection_type=ETHERNET_CONNECTION_TYPE)
    except NetworkManagerError as exc:
        logger.warning("could not list ethernet profiles at startup: %s", exc)
        return

    for profile in profiles:
        name = profile["name"]
        try:
            new_name = replace_netplan_ethernet_profile(config, name, mac_address)
            logger.info("ethernet MAC enforced on profile '%s' (active name: '%s')", name, new_name)
        except NetworkManagerError as exc:
            logger.warning("could not enforce MAC on profile '%s': %s", name, exc)


if __name__ == "__main__":
    _enforce_ethernet_mac()
    watchdog = FailoverWatchdog(app.config)
    watchdog.run_forever()
