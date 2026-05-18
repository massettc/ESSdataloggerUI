from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any

from flask import Flask, redirect, url_for

from .auth import auth_bp
from .config import Config
from .routes.network import network_bp
from .services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    bring_up_connection,
    list_connection_profiles,
    set_connection_ethernet_mac,
)


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    _configure_logging(app)

    logger = logging.getLogger("pi_network_admin")
    logger.info("pi-network-admin app initialization started")

    _enforce_ethernet_mac(app.config)

    app.register_blueprint(auth_bp)
    app.register_blueprint(network_bp)

    @app.context_processor
    def inject_shell_context() -> dict[str, str]:
        configured_hostname = str(app.config.get("DEVICE_HOSTNAME", "")).strip()
        repo_path = app.config.get("REPO_PATH") or str(Path(app.root_path).parent)
        version_file = Path(repo_path) / "VERSION"
        try:
            app_version = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            app_version = ""
        return {"device_hostname": configured_hostname or socket.gethostname(), "app_version": app_version}

    @app.route("/")
    def index():
        return redirect(url_for("network.datalogger"))

    return app


def _enforce_ethernet_mac(config: dict[str, Any]) -> None:
    """On startup, pin the cloned MAC address on all ethernet connection profiles.

    This corrects any profile that was modified via the OS UI with a 'clone from
    host' or random MAC setting, ensuring eth0 always uses the correct hardware
    address after a service restart.
    """
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
            logger.info("enforced MAC %s on ethernet profile '%s'", mac_address, name)
        except NetworkManagerError as exc:
            logger.warning("could not set MAC on profile '%s': %s", name, exc)
            continue

        # Bring the connection up immediately so NM applies the new cloned MAC
        # in a single controlled reconnect now, before the watchdog starts
        # calling `device reapply`. Without this, every watchdog reapply cycle
        # detects the profile MAC differs from the live interface MAC and triggers
        # a full reconnect, causing eth0 to cycle repeatedly.
        if profile.get("active"):
            try:
                bring_up_connection(config, name)
                logger.info("reactivated '%s' to apply new MAC", name)
            except NetworkManagerError as exc:
                logger.warning("could not reactivate '%s' after MAC change: %s", name, exc)


def _configure_logging(app: Flask) -> None:
    log_path = Path(app.config["LOG_PATH"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("pi_network_admin")

    if any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        return

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    app.logger.setLevel(logging.DEBUG)
    app.logger.addHandler(file_handler)

    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.propagate = False
