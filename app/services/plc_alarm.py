from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import BASE_DIR
from app.services.datalogger_manager import get_datalogger_status

logger = logging.getLogger("pi_network_admin.plc_alarm")


class PlcAlarmError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlcAlarmSettings:
    enabled: bool
    host: str
    port: int
    unit_id: int
    register_address: int
    poll_interval_seconds: float
    alarm_after_seconds: float
    clear_after_seconds: float
    request_timeout_seconds: float
    trigger_on_backlog: bool
    trigger_on_error: bool
    alarm_value: int
    clear_value: int


def default_plc_alarm_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "host": "192.168.0.10",
        "port": 502,
        "unit_id": 1,
        "register_address": 1000,
        "poll_interval_seconds": 5,
        "alarm_after_seconds": 30,
        "clear_after_seconds": 30,
        "request_timeout_seconds": 3,
        "trigger_on_backlog": True,
        "trigger_on_error": True,
        "alarm_value": 1,
        "clear_value": 0,
    }


def get_plc_alarm_config_path(config: dict[str, Any]) -> Path:
    configured = str(config.get("PLC_ALARM_CONFIG_FILE") or BASE_DIR / "config" / "plc_alarm.json").strip()
    path = Path(configured)
    if path.is_absolute():
        return path
    return Path(str(config.get("REPO_PATH") or BASE_DIR)) / path


def load_plc_alarm_settings(config: dict[str, Any]) -> PlcAlarmSettings:
    defaults = default_plc_alarm_settings()
    config_path = get_plc_alarm_config_path(config)
    payload: dict[str, Any] = {}

    if config_path.exists():
        raw_content = config_path.read_text(encoding="utf-8")
        if raw_content.strip():
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                raise PlcAlarmError(f"Invalid PLC alarm JSON in {config_path}: {exc.msg}") from exc
            if not isinstance(parsed, dict):
                raise PlcAlarmError(f"PLC alarm config must be a JSON object: {config_path}")
            payload = parsed

    merged = {**defaults, **payload}
    try:
        return PlcAlarmSettings(
            enabled=bool(merged.get("enabled", defaults["enabled"])),
            host=str(merged.get("host", defaults["host"])).strip(),
            port=int(merged.get("port", defaults["port"])),
            unit_id=int(merged.get("unit_id", defaults["unit_id"])),
            register_address=int(merged.get("register_address", defaults["register_address"])),
            poll_interval_seconds=max(1.0, float(merged.get("poll_interval_seconds", defaults["poll_interval_seconds"]))),
            alarm_after_seconds=max(0.0, float(merged.get("alarm_after_seconds", defaults["alarm_after_seconds"]))),
            clear_after_seconds=max(0.0, float(merged.get("clear_after_seconds", defaults["clear_after_seconds"]))),
            request_timeout_seconds=max(0.1, float(merged.get("request_timeout_seconds", defaults["request_timeout_seconds"]))),
            trigger_on_backlog=bool(merged.get("trigger_on_backlog", defaults["trigger_on_backlog"])),
            trigger_on_error=bool(merged.get("trigger_on_error", defaults["trigger_on_error"])),
            alarm_value=int(merged.get("alarm_value", defaults["alarm_value"])),
            clear_value=int(merged.get("clear_value", defaults["clear_value"])),
        )
    except (TypeError, ValueError) as exc:
        raise PlcAlarmError(f"PLC alarm config has invalid value types: {config_path}") from exc


def ensure_plc_alarm_config_file(config: dict[str, Any]) -> Path:
    config_path = get_plc_alarm_config_path(config)
    if config_path.exists():
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(default_plc_alarm_settings(), indent=2) + "\n", encoding="utf-8")
    return config_path


def cloud_delivery_unhealthy(status: dict[str, Any], settings: PlcAlarmSettings) -> bool:
    if settings.trigger_on_error:
        mqtt_error = str(status.get("mqtt_logger", {}).get("error", "")).strip()
        plc_error = str(status.get("plc_logger", {}).get("error", "")).strip()
        if mqtt_error or plc_error:
            return True

    if settings.trigger_on_backlog:
        for key in ("mqtt_logger", "plc_logger"):
            queue_size = status.get(key, {}).get("queue_size")
            if isinstance(queue_size, int) and queue_size > 0:
                return True

    return False


class PlcAlarmWorker:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._unhealthy_since: float | None = None
        self._healthy_since: float | None = None
        self._alarm_active = False
        self._last_written_state: bool | None = None

    def run_forever(self) -> None:
        logger.info("PLC alarm worker started config=%s", get_plc_alarm_config_path(self.config))
        while True:
            settings = self._safe_load_settings()
            if settings and settings.enabled:
                self.run_once(settings=settings)
                sleep_seconds = settings.poll_interval_seconds
            else:
                sleep_seconds = 5.0
            time.sleep(sleep_seconds)

    def run_once(self, settings: PlcAlarmSettings | None = None, now: float | None = None) -> dict[str, Any]:
        settings = settings or load_plc_alarm_settings(self.config)
        current_time = time.monotonic() if now is None else now

        if not settings.enabled:
            self._unhealthy_since = None
            self._healthy_since = None
            return {"status": "disabled", "alarm_active": self._alarm_active}

        status = get_datalogger_status(self.config)
        unhealthy = cloud_delivery_unhealthy(status, settings)
        desired_alarm = self._update_alarm_state(unhealthy, settings, current_time)

        write_result = "unchanged"
        if desired_alarm != self._last_written_state:
            self._write_alarm_state(settings, desired_alarm)
            self._last_written_state = desired_alarm
            write_result = "written"

        return {
            "status": "alarm" if desired_alarm else "normal",
            "alarm_active": desired_alarm,
            "cloud_unhealthy": unhealthy,
            "write_result": write_result,
        }

    def _update_alarm_state(self, unhealthy: bool, settings: PlcAlarmSettings, current_time: float) -> bool:
        if unhealthy:
            self._healthy_since = None
            if self._unhealthy_since is None:
                self._unhealthy_since = current_time
            if current_time - self._unhealthy_since >= settings.alarm_after_seconds:
                self._alarm_active = True
            return self._alarm_active

        self._unhealthy_since = None
        if self._healthy_since is None:
            self._healthy_since = current_time
        if current_time - self._healthy_since >= settings.clear_after_seconds:
            self._alarm_active = False
        return self._alarm_active

    def _write_alarm_state(self, settings: PlcAlarmSettings, alarm_active: bool) -> None:
        value = settings.alarm_value if alarm_active else settings.clear_value
        write_modbus_register(settings, value)
        logger.info(
            "PLC alarm register updated host=%s port=%s unit_id=%s register=%s value=%s",
            settings.host,
            settings.port,
            settings.unit_id,
            settings.register_address,
            value,
        )

    def _safe_load_settings(self) -> PlcAlarmSettings | None:
        try:
            return load_plc_alarm_settings(self.config)
        except PlcAlarmError:
            logger.exception("Unable to load PLC alarm settings")
            return None


def write_modbus_register(settings: PlcAlarmSettings, value: int) -> None:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as exc:
        raise PlcAlarmError("pymodbus is required for PLC alarm support") from exc

    client = ModbusTcpClient(host=settings.host, port=settings.port, timeout=settings.request_timeout_seconds)
    try:
        if not client.connect():
            raise PlcAlarmError(f"Unable to connect to PLC at {settings.host}:{settings.port}")
        response = client.write_register(address=settings.register_address, value=value, slave=settings.unit_id)
        if response.isError():
            raise PlcAlarmError(f"PLC write failed for register {settings.register_address}: {response}")
    finally:
        client.close()