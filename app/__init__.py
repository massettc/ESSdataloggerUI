from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any

from flask import Flask, redirect, url_for

from .auth import auth_bp
from .config import Config
from .routes.network import network_bp


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    _configure_logging(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(network_bp)

    @app.context_processor
    def inject_shell_context() -> dict[str, str]:
        configured_hostname = str(app.config.get("DEVICE_HOSTNAME", "")).strip()
        return {"device_hostname": configured_hostname or socket.gethostname()}

    @app.route("/")
    def index():
        return redirect(url_for("network.datalogger"))

    return app


def _configure_logging(app: Flask) -> None:
    log_path = Path(app.config["LOG_PATH"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("pi_network_admin")

    if any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        return

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.propagate = False
