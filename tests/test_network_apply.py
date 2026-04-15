from app.services import network_apply


def test_apply_wifi_settings_rolls_back_on_failed_verify(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "connect_wifi", lambda config, ssid, password, hidden: calls.append((ssid, hidden)))
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: calls.append(("rollback", name)))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: False)

    result = network_apply.apply_wifi_settings({"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01}, "NewWiFi", "badpass", False)

    assert result["success"] is False
    assert "did not become active" in result["message"]
    assert calls[-1] == ("rollback", "OldWiFi")


def test_apply_wifi_settings_returns_nmcli_error_details(monkeypatch):
    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: None)

    def raise_error(config, ssid, password, hidden):
        raise network_apply.NetworkManagerError("Not authorized to control networking.")

    monkeypatch.setattr(network_apply, "connect_wifi", raise_error)

    result = network_apply.apply_wifi_settings({"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01}, "NewWiFi", "pw", False)

    assert result["success"] is False
    assert "Not authorized" in result["message"]


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


def test_apply_ethernet_settings_updates_static_ipv4_and_restores_previous_profile(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "get_connection_ipv4_config", lambda config, connection_name: {
        "method": "auto",
        "address": "",
        "prefix": "",
        "gateway": "",
        "dns": "",
    })
    monkeypatch.setattr(network_apply, "set_connection_ipv4_config", lambda config, **kwargs: calls.append(("modify", kwargs)))
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: calls.append(("up", name)))
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: False)

    result = network_apply.apply_ethernet_settings(
        {"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        connection_name="Wired connection 1",
        ip_method="manual",
        ip_address="192.168.2.10",
        ip_prefix="24",
        gateway="192.168.2.1",
        dns="8.8.8.8",
    )

    assert result["success"] is False
    assert calls[0][0] == "modify"
    assert calls[0][1]["method"] == "manual"
    assert calls[-2][0] == "modify"
    assert calls[-2][1]["method"] == "auto"
    assert calls[-1] == ("up", "Wired connection 1")
