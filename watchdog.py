import logging

from app import create_app
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    ensure_ethernet_profile,
    list_connection_profiles,
    replace_netplan_ethernet_profile,
)
from app.services.network_watchdog import FailoverWatchdog


app = create_app()


def _enforce_ethernet_mac() -> None:
    """Ensure the persistent ethernet NM profile exists with the correct MAC.

    The install script removes eth0 from netplan so there is no competing
    netplan-managed profile.  This function simply guarantees our profile is
    present and has the right cloned-mac-address on every startup.

    For Pis that were deployed before the install script added the netplan-
    ownership step, we also scan for any lingering netplan-* ethernet profiles
    and clean them up via replace_netplan_ethernet_profile.
    """
    config = app.config
    mac_address = config.get("ETHERNET_MAC_ADDRESS", "")
    interface = config.get("ETHERNET_INTERFACE", "eth0")
    if not mac_address:
        return

    logger = logging.getLogger("pi_network_admin")

    # Ensure our persistent profile exists and has the correct MAC.
    try:
        ensure_ethernet_profile(config, interface, mac_address)
        logger.info("ethernet profile '%s' ensured with MAC %s", interface, mac_address)
    except NetworkManagerError as exc:
        logger.warning("could not ensure ethernet profile '%s': %s", interface, exc)

    # Clean up any lingering netplan-managed profiles for this interface
    # (handles Pis not yet updated via the new install.sh).
    try:
        profiles = list_connection_profiles(config, connection_type=ETHERNET_CONNECTION_TYPE)
    except NetworkManagerError as exc:
        logger.warning("could not list ethernet profiles: %s", exc)
        return

    for profile in profiles:
        name = profile["name"]
        if name.startswith("netplan-") and profile.get("device") in (interface, ""):
            try:
                replace_netplan_ethernet_profile(config, name, mac_address)
                logger.info("cleaned up legacy netplan profile '%s'", name)
            except NetworkManagerError as exc:
                logger.warning("could not clean up netplan profile '%s': %s", name, exc)


if __name__ == "__main__":
    _enforce_ethernet_mac()
    watchdog = FailoverWatchdog(app.config)
    watchdog.run_forever()
