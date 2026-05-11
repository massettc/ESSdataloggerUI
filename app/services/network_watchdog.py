from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from app.services.network_apply import ensure_connection_active
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    bring_up_connection,
    connect_device,
    get_active_connection,
    list_connection_profiles,
    reapply_device,
    set_connection_metric,
    set_connection_never_default,
)


logger = logging.getLogger("pi_network_admin.watchdog")


class FailoverWatchdog:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.primary_failures = 0
        self.primary_recoveries = 0
        self.using_backup = False
        # Track connections that have failed with secrets errors so we don't
        # hammer NM repeatedly, which puts the connection into activation backoff
        # and blocks the user from manually reconnecting.
        self._secrets_failed_connections: set[str] = set()

    def run_forever(self) -> None:
        if not self.config.get("WATCHDOG_ENABLED", True):
            logger.info("watchdog disabled by configuration")
            return

        primary_name = self._configured_connection_name(self.config["PRIMARY_INTERFACE"])
        primary_healthy = self._interface_is_healthy(self.config["PRIMARY_INTERFACE"], primary_name)
        self.using_backup = self._is_backup_active() and not primary_healthy
        self._configure_route_metrics(prefer_backup=self.using_backup)
        self._suppress_extra_ethernet_defaults()
        logger.info(
            "watchdog started primary=%s backup=%s target=%s interval=%s using_backup=%s",
            self.config["PRIMARY_INTERFACE"],
            self.config["BACKUP_INTERFACE"],
            self.config["WATCHDOG_TARGET_HOST"],
            self.config["WATCHDOG_INTERVAL_SECONDS"],
            self.using_backup,
        )

        while True:
            self.run_once()
            time.sleep(self.config["WATCHDOG_INTERVAL_SECONDS"])

    def run_once(self) -> dict[str, Any]:
        primary_name = self._configured_connection_name(self.config["PRIMARY_INTERFACE"])
        primary_healthy = self._interface_is_healthy(self.config["PRIMARY_INTERFACE"], primary_name)

        if primary_healthy:
            self.primary_failures = 0
            if self.using_backup:
                self.primary_recoveries += 1
                if self.primary_recoveries >= self.config["WATCHDOG_RECOVERY_THRESHOLD"]:
                    switched = self._activate_interface(self.config["PRIMARY_INTERFACE"], primary_name)
                    if switched:
                        self._configure_route_metrics(prefer_backup=False)
                        self.using_backup = False
                        self.primary_recoveries = 0
                        logger.info("restored primary interface=%s", self.config["PRIMARY_INTERFACE"])
                        return {"status": "restored-primary"}
                return {"status": "primary-recovering", "recovery_count": self.primary_recoveries}

            return {"status": "primary-ok"}

        self.primary_recoveries = 0
        self.primary_failures += 1
        logger.warning(
            "primary health check failed interface=%s count=%s",
            self.config["PRIMARY_INTERFACE"],
            self.primary_failures,
        )

        if self.primary_failures < self.config["WATCHDOG_FAILURE_THRESHOLD"]:
            return {"status": "primary-degraded", "failure_count": self.primary_failures}

        self.primary_failures = 0
        backup_name = self._configured_connection_name(self.config["BACKUP_INTERFACE"])
        switched = self._activate_interface(self.config["BACKUP_INTERFACE"], backup_name)
        if switched:
            self._configure_route_metrics(prefer_backup=True)
            self.using_backup = True
            logger.warning("failed over to backup interface=%s", self.config["BACKUP_INTERFACE"])
            return {"status": "failed-over"}

        logger.error("backup activation failed interface=%s", self.config["BACKUP_INTERFACE"])
        return {"status": "backup-unavailable"}

    def _configure_route_metrics(self, prefer_backup: bool = False) -> None:
        primary_metric = self.config["PRIMARY_ROUTE_METRIC"]
        backup_metric = self.config["BACKUP_ROUTE_METRIC"]

        if self.config.get("PREFER_WLAN_FOR_INTERNET", False) and not prefer_backup:
            metric_by_interface = {
                self.config["WIFI_INTERFACE"]: primary_metric,
                self.config["ETHERNET_INTERFACE"]: backup_metric,
            }
            interfaces = (self.config["WIFI_INTERFACE"], self.config["ETHERNET_INTERFACE"])
        else:
            metric_by_interface = {
                self.config["PRIMARY_INTERFACE"]: backup_metric if prefer_backup else primary_metric,
                self.config["BACKUP_INTERFACE"]: primary_metric if prefer_backup else backup_metric,
            }
            interfaces = (self.config["PRIMARY_INTERFACE"], self.config["BACKUP_INTERFACE"])

        for interface_name in interfaces:
            connection_name = self._configured_connection_name(interface_name)
            if not connection_name:
                continue

            metric = metric_by_interface[interface_name]
            try:
                set_connection_metric(self.config, connection_name, metric)
                set_connection_never_default(
                    self.config,
                    connection_name,
                    enabled=self._should_never_default(interface_name, prefer_backup),
                )
                reapply_device(self.config, interface_name)
            except NetworkManagerError as exc:
                logger.warning(
                    "unable to apply routing policy interface=%s connection=%s metric=%s error=%s",
                    interface_name,
                    connection_name,
                    metric,
                    exc,
                )

    def _should_never_default(self, interface_name: str, prefer_backup: bool) -> bool:
        if self.config.get("PREFER_WLAN_FOR_INTERNET", False):
            # Preserve manual Ethernet gateways: prefer Wi-Fi via route metrics,
            # not by forcing ethernet never-default=yes.
            return False
        if prefer_backup:
            return interface_name == self.config["PRIMARY_INTERFACE"]
        return interface_name == self.config["BACKUP_INTERFACE"]

    def _configured_connection_name(self, interface_name: str) -> str | None:
        if interface_name == self.config["PRIMARY_INTERFACE"]:
            configured = self.config.get("PRIMARY_CONNECTION_NAME") or None
        elif interface_name == self.config["BACKUP_INTERFACE"]:
            configured = self.config.get("BACKUP_CONNECTION_NAME") or None
        else:
            configured = None

        if configured:
            return configured

        active = get_active_connection(self.config, interface_name)
        if active:
            return active["name"]
        return None

    def _interface_is_healthy(self, interface_name: str, connection_name: str | None) -> bool:
        if not ensure_connection_active(self.config, interface_name, connection_name):
            return False

        command = [
            self.config["PING_BIN"],
            "-I",
            interface_name,
            "-c",
            "1",
            "-W",
            str(self.config["WATCHDOG_PING_TIMEOUT_SECONDS"]),
            self.config["WATCHDOG_TARGET_HOST"],
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.config["WATCHDOG_PING_TIMEOUT_SECONDS"] + 1,
                check=False,
            )
        except FileNotFoundError:
            logger.error("ping binary not found: %s", self.config["PING_BIN"])
            return False
        except subprocess.TimeoutExpired:
            return False

        return completed.returncode == 0

    def _activate_interface(self, interface_name: str, connection_name: str | None) -> bool:
        if ensure_connection_active(self.config, interface_name, connection_name):
            # If the connection is now active, clear any secrets-failed record for it
            if connection_name and connection_name in self._secrets_failed_connections:
                self._secrets_failed_connections.discard(connection_name)
                logger.info("cleared secrets-failed record for connection=%s", connection_name)
            return self._interface_is_healthy(interface_name, connection_name)

        if connection_name and connection_name in self._secrets_failed_connections:
            logger.warning(
                "skipping activation of connection=%s: previously failed with secrets error; "
                "user must enter password via the web UI",
                connection_name,
            )
            return False

        try:
            if connection_name:
                bring_up_connection(self.config, connection_name)
            else:
                connect_device(self.config, interface_name)
        except NetworkManagerError as exc:
            error_text = str(exc).lower()
            if "secrets were required" in error_text or "no secrets" in error_text:
                if connection_name:
                    self._secrets_failed_connections.add(connection_name)
                logger.warning(
                    "interface activation failed with secrets error — "
                    "will not retry until user reconnects via web UI. "
                    "interface=%s connection=%s error=%s",
                    interface_name,
                    connection_name,
                    exc,
                )
            else:
                logger.error(
                    "interface activation failed interface=%s connection=%s error=%s",
                    interface_name,
                    connection_name,
                    exc,
                )
            return False

        return self._interface_is_healthy(interface_name, connection_name)

    def _is_backup_active(self) -> bool:
        backup_name = self._configured_connection_name(self.config["BACKUP_INTERFACE"])
        return ensure_connection_active(self.config, self.config["BACKUP_INTERFACE"], backup_name)

    def _suppress_extra_ethernet_defaults(self) -> None:
        """Set never-default on ethernet connections that aren't the designated backup.

        Prevents secondary ethernet ports (e.g. eth1) from adding a default route
        and competing with wlan0 or the designated backup for internet traffic.
        """
        backup_interface = self.config.get("BACKUP_INTERFACE", "")
        backup_name = self._configured_connection_name(backup_interface) or ""

        try:
            profiles = list_connection_profiles(self.config, connection_type=ETHERNET_CONNECTION_TYPE)
        except NetworkManagerError as exc:
            logger.warning("unable to list ethernet profiles for suppression: %s", exc)
            return

        for profile in profiles:
            if profile["name"] == backup_name or profile["device"] == backup_interface:
                continue
            try:
                set_connection_never_default(self.config, profile["name"], enabled=True)
                logger.debug("suppressed default route on extra ethernet connection=%s", profile["name"])
            except NetworkManagerError as exc:
                logger.warning(
                    "unable to suppress default route on connection=%s: %s",
                    profile["name"],
                    exc,
                )
