from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from app.services.network_apply import ensure_connection_active
from app.services.network_manager import (
    NetworkManagerError,
    bring_up_connection,
    connect_device,
    get_active_connection,
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

    def run_forever(self) -> None:
        if not self.config.get("WATCHDOG_ENABLED", True):
            logger.info("watchdog disabled by configuration")
            return

        primary_name = self._configured_connection_name(self.config["PRIMARY_INTERFACE"])
        primary_healthy = self._interface_is_healthy(self.config["PRIMARY_INTERFACE"], primary_name)
        self.using_backup = self._is_backup_active() and not primary_healthy
        self._configure_route_metrics(prefer_backup=self.using_backup)
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
            return interface_name == self.config["ETHERNET_INTERFACE"]
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
        try:
            if connection_name:
                bring_up_connection(self.config, connection_name)
            else:
                connect_device(self.config, interface_name)
        except NetworkManagerError as exc:
            logger.error(
                "interface activation failed interface=%s connection=%s error=%s",
                interface_name,
                connection_name,
                exc,
            )
            return False

        return ensure_connection_active(self.config, interface_name, connection_name)

    def _is_backup_active(self) -> bool:
        backup_name = self._configured_connection_name(self.config["BACKUP_INTERFACE"])
        return ensure_connection_active(self.config, self.config["BACKUP_INTERFACE"], backup_name)
