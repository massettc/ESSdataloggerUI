import subprocess

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


def test_scan_wifi_networks_retries_when_first_result_is_sparse(monkeypatch):
    outputs = iter([
        "",
        "*:PlantWiFi:84:WPA2\n",
        "*:PlantWiFi:84:WPA2\n:Guest:48:Open\n",
    ])

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: next(outputs))

    networks = network_manager.scan_wifi_networks({"WIFI_INTERFACE": "wlan0"})

    assert [network["ssid"] for network in networks] == ["PlantWiFi", "Guest"]


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


def test_get_connection_ipv4_config_parses_manual_values(monkeypatch):
    sample_output = "manual\n192.168.10.25/24\n192.168.10.1\n8.8.8.8,1.1.1.1\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    settings = network_manager.get_connection_ipv4_config({}, "Wired connection 1")

    assert settings["method"] == "manual"
    assert settings["address"] == "192.168.10.25"
    assert settings["prefix"] == "24"
    assert settings["gateway"] == "192.168.10.1"
    assert settings["dns"] == "8.8.8.8,1.1.1.1"


def test_set_connection_ipv4_config_uses_nmcli_modify(monkeypatch):
    calls = []

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: calls.append(arguments) or "")

    network_manager.set_connection_ipv4_config(
        {},
        connection_name="Wired connection 1",
        method="manual",
        address="192.168.50.20",
        prefix="24",
        gateway="192.168.50.1",
        dns="8.8.8.8,1.1.1.1",
    )

    assert calls == [[
        "connection",
        "modify",
        "Wired connection 1",
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        "192.168.50.20/24",
        "ipv4.gateway",
        "192.168.50.1",
        "ipv4.dns",
        "8.8.8.8,1.1.1.1",
    ]]


def test_build_nmcli_command_uses_sudo_for_mutating_commands():
    command = network_manager._build_nmcli_command(
        {"NMCLI_BIN": "nmcli", "USE_SUDO_FOR_NMCLI": True},
        ["device", "wifi", "connect", "PlantWiFi"],
    )

    assert command[:3] == ["sudo", "-n", "nmcli"]


def test_build_nmcli_command_skips_sudo_for_read_only_commands():
    command = network_manager._build_nmcli_command(
        {"NMCLI_BIN": "nmcli", "USE_SUDO_FOR_NMCLI": True},
        ["device", "wifi", "list"],
    )

    assert command == ["nmcli", "device", "wifi", "list"]
