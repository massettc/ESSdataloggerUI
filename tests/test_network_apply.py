from app.services import network_apply


def test_apply_wifi_settings_rolls_back_on_failed_verify(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "connect_wifi", lambda config, ssid, password, hidden: calls.append((ssid, hidden)))
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: calls.append(("rollback", name)))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: False)

    result = network_apply.apply_wifi_settings({"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01}, "NewWiFi", "badpass", False)

    assert result["success"] is False
    assert calls[-1] == ("rollback", "OldWiFi")


def test_apply_wifi_settings_succeeds(monkeypatch):
    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "connect_wifi", lambda config, ssid, password, hidden: None)
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)

    result = network_apply.apply_wifi_settings({"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01}, "NewWiFi", "goodpass", False)

    assert result["success"] is True
    assert "Connected to NewWiFi" in result["message"]


def test_apply_ethernet_settings_reconnects_device(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "connect_device", lambda config, interface_name: calls.append(("connect", interface_name)))
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: True)

    result = network_apply.apply_ethernet_settings({"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01})

    assert result["success"] is True
    assert calls == [("connect", "eth0")]


def test_apply_ethernet_settings_rolls_back_on_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: calls.append(name))
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: False)

    result = network_apply.apply_ethernet_settings(
        {"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        connection_name="Static LAN",
    )

    assert result["success"] is False
    assert calls == ["Static LAN", "Wired connection 1"]
