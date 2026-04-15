from app.services import network_manager


def test_split_escaped_fields_handles_colons_in_ssid():
    line = r"*:Office\:2G:72:WPA2"
    parts = network_manager._split_escaped_fields(line)
    assert parts == ["*", "Office:2G", "72", "WPA2"]


def test_scan_wifi_networks_sorts_active_first(monkeypatch):
    sample_output = "*:PlantWiFi:84:WPA2\n:Guest:48:Open\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    networks = network_manager.scan_wifi_networks({"WIFI_INTERFACE": "wlan0"})

    assert networks[0]["ssid"] == "PlantWiFi"
    assert networks[0]["in_use"] is True
    assert networks[1]["security"] == "Open"


def test_list_connection_profiles_filters_by_type_and_interface(monkeypatch):
    sample_output = "Office WiFi:802-11-wireless:wlan0\nWired connection 1:802-3-ethernet:eth0\nSpare LAN:802-3-ethernet:--\nVPN:wireguard:--\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    profiles = network_manager.list_connection_profiles(
        {"ETHERNET_INTERFACE": "eth0"},
        connection_type=network_manager.ETHERNET_CONNECTION_TYPE,
        interface_name="eth0",
    )

    assert [profile["name"] for profile in profiles] == ["Wired connection 1", "Spare LAN"]
    assert profiles[0]["active"] is True
    assert profiles[1]["device"] == ""


def test_get_active_ethernet_connection_returns_matching_profile(monkeypatch):
    sample_output = "Office WiFi:wlan0:802-11-wireless\nWired connection 1:eth0:802-3-ethernet\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    active = network_manager.get_active_ethernet_connection({"ETHERNET_INTERFACE": "eth0"})

    assert active == {"name": "Wired connection 1", "device": "eth0", "type": "802-3-ethernet"}
