from pathlib import Path
import sys
import types

import pytest

from app.services import plc_alarm


def test_load_plc_alarm_settings_reads_json(tmp_path: Path):
    config_path = tmp_path / "plc_alarm.json"
    config_path.write_text(
        """
        {
          "enabled": true,
          "host": "192.168.0.10",
          "port": 502,
          "unit_id": 1,
          "register_address": 1000,
          "alarm_after_seconds": 30
        }
        """.strip(),
        encoding="utf-8",
    )

    settings = plc_alarm.load_plc_alarm_settings({"PLC_ALARM_CONFIG_FILE": str(config_path)})

    assert settings.enabled is True
    assert settings.host == "192.168.0.10"
    assert settings.unit_id == 1
    assert settings.register_address == 1000
    assert settings.alarm_after_seconds == 30


def test_cloud_delivery_unhealthy_detects_backlog_and_error():
    settings = plc_alarm.load_plc_alarm_settings({})

    backlog_status = {
        "mqtt_logger": {"queue_size": 4, "error": ""},
        "plc_logger": {"queue_size": 0, "error": ""},
    }
    error_status = {
        "mqtt_logger": {"queue_size": 0, "error": "publish failed"},
        "plc_logger": {"queue_size": 0, "error": ""},
    }

    assert plc_alarm.cloud_delivery_unhealthy(backlog_status, settings) is True
    assert plc_alarm.cloud_delivery_unhealthy(error_status, settings) is True


def test_worker_waits_for_alarm_threshold(monkeypatch):
    worker = plc_alarm.PlcAlarmWorker({})
    settings = plc_alarm.load_plc_alarm_settings({})
    writes: list[int] = []

    monkeypatch.setattr(
        plc_alarm,
        "get_datalogger_status",
        lambda config: {"mqtt_logger": {"queue_size": 2, "error": ""}, "plc_logger": {"queue_size": 0, "error": ""}},
    )
    monkeypatch.setattr(plc_alarm, "write_modbus_register", lambda current_settings, value: writes.append(value))

    first = worker.run_once(settings=settings, now=0)
    second = worker.run_once(settings=settings, now=10)
    third = worker.run_once(settings=settings, now=31)

    assert first["alarm_active"] is False
    assert second["alarm_active"] is False
    assert third["alarm_active"] is True
    assert writes == [0, 1]


def test_worker_clears_after_recovery_threshold(monkeypatch):
    worker = plc_alarm.PlcAlarmWorker({})
    settings = plc_alarm.load_plc_alarm_settings({})
    writes: list[int] = []
    statuses = iter(
        [
            {"mqtt_logger": {"queue_size": 2, "error": ""}, "plc_logger": {"queue_size": 0, "error": ""}},
            {"mqtt_logger": {"queue_size": 2, "error": ""}, "plc_logger": {"queue_size": 0, "error": ""}},
            {"mqtt_logger": {"queue_size": 0, "error": ""}, "plc_logger": {"queue_size": 0, "error": ""}},
            {"mqtt_logger": {"queue_size": 0, "error": ""}, "plc_logger": {"queue_size": 0, "error": ""}},
        ]
    )

    monkeypatch.setattr(plc_alarm, "get_datalogger_status", lambda config: next(statuses))
    monkeypatch.setattr(plc_alarm, "write_modbus_register", lambda current_settings, value: writes.append(value))

    worker.run_once(settings=settings, now=0)
    worker.run_once(settings=settings, now=31)
    third = worker.run_once(settings=settings, now=40)
    fourth = worker.run_once(settings=settings, now=71)

    assert third["alarm_active"] is True
    assert fourth["alarm_active"] is False
    assert writes == [0, 1, 0]


def test_write_modbus_register_raises_for_connection_failure(monkeypatch):
    settings = plc_alarm.load_plc_alarm_settings({})

    class FakeClient:
        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout

        def connect(self):
            return False

        def close(self):
            return None

    fake_module = types.SimpleNamespace(ModbusTcpClient=FakeClient)
    monkeypatch.setitem(sys.modules, "pymodbus.client", fake_module)

    with pytest.raises(plc_alarm.PlcAlarmError):
        plc_alarm.write_modbus_register(settings, 1)