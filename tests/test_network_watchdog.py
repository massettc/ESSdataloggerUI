from app.services import network_watchdog


BASE_CONFIG = {
    "WATCHDOG_ENABLED": True,
    "WATCHDOG_TARGET_HOST": "1.1.1.1",
    "WATCHDOG_INTERVAL_SECONDS": 10,
    "WATCHDOG_PING_TIMEOUT_SECONDS": 2,
    "WATCHDOG_FAILURE_THRESHOLD": 2,
    "WATCHDOG_RECOVERY_THRESHOLD": 2,
    "PRIMARY_INTERFACE": "eth0",
    "BACKUP_INTERFACE": "wlan0",
    "PRIMARY_CONNECTION_NAME": "Wired connection 1",
    "BACKUP_CONNECTION_NAME": "PlantWiFi",
    "PRIMARY_ROUTE_METRIC": 100,
    "BACKUP_ROUTE_METRIC": 200,
    "PING_BIN": "ping",
    "WIFI_INTERFACE": "wlan0",
    "ETHERNET_INTERFACE": "eth0",
}


def test_watchdog_fails_over_after_threshold(monkeypatch):
    watchdog = network_watchdog.FailoverWatchdog(dict(BASE_CONFIG))
    activations = []
    health_checks = iter([False, False])

    monkeypatch.setattr(watchdog, "_interface_is_healthy", lambda interface_name, connection_name: next(health_checks))
    monkeypatch.setattr(watchdog, "_activate_interface", lambda interface_name, connection_name: activations.append((interface_name, connection_name)) or True)

    first = watchdog.run_once()
    second = watchdog.run_once()

    assert first["status"] == "primary-degraded"
    assert second["status"] == "failed-over"
    assert activations == [("wlan0", "PlantWiFi")]
    assert watchdog.using_backup is True


def test_watchdog_restores_primary_after_recovery_threshold(monkeypatch):
    watchdog = network_watchdog.FailoverWatchdog(dict(BASE_CONFIG))
    watchdog.using_backup = True
    activations = []

    monkeypatch.setattr(watchdog, "_interface_is_healthy", lambda interface_name, connection_name: True)
    monkeypatch.setattr(watchdog, "_activate_interface", lambda interface_name, connection_name: activations.append((interface_name, connection_name)) or True)

    first = watchdog.run_once()
    second = watchdog.run_once()

    assert first["status"] == "primary-recovering"
    assert second["status"] == "restored-primary"
    assert activations == [("eth0", "Wired connection 1")]
    assert watchdog.using_backup is False


def test_watchdog_uses_active_connection_name_when_not_configured(monkeypatch):
    config = dict(BASE_CONFIG)
    config["PRIMARY_CONNECTION_NAME"] = ""
    config["BACKUP_CONNECTION_NAME"] = ""
    watchdog = network_watchdog.FailoverWatchdog(config)

    monkeypatch.setattr(
        network_watchdog,
        "get_active_connection",
        lambda config, interface_name: {"name": f"{interface_name}-profile", "device": interface_name, "type": "test"},
    )

    assert watchdog._configured_connection_name("eth0") == "eth0-profile"
    assert watchdog._configured_connection_name("wlan0") == "wlan0-profile"
