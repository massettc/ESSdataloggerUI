import logging

from app import create_app
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    list_connection_profiles,
    persist_connection_to_etc,
    set_connection_ethernet_mac,
)
from app.services.network_watchdog import FailoverWatchdog


app = create_app()


def _enforce_ethernet_mac() -> None:
    """Pin the cloned MAC address on all ethernet profiles.

    Called once here (in the watchdog process only) so NM profile changes are
    made by a single process and don't race against gunicorn workers.
    `connection modify` updates the profile file; NM applies the new MAC on the
    next natural connection activation without triggering an immediate reconnect.
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
            set_connection_ethernet_mac(config, name, mac_address)
            persist_connection_to_etc(config, name)
            logger.info("enforced MAC %s on ethernet profile '%s'", mac_address, name)
        except NetworkManagerError as exc:
            logger.warning("could not set MAC on profile '%s': %s", name, exc)


if __name__ == "__main__":
    _enforce_ethernet_mac()
    watchdog = FailoverWatchdog(app.config)
    watchdog.run_forever()
