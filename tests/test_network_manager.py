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



def test_scan_wifi_networks_uses_short_cache(monkeypatch):
    calls = {"count": 0}
    sample_output = "*:PlantWiFi:84:WPA2\n:Guest:48:Open\n"

    monkeypatch.setattr(network_manager, "_rescan_wifi", lambda config, interface: None)

    def fake_run(config, arguments):
        calls["count"] += 1
        return sample_output

    monkeypatch.setattr(network_manager, "_run_nmcli", fake_run)

    config = {"WIFI_INTERFACE": "wlan0", "WIFI_SCAN_CACHE_SECONDS": 30, "REPO_PATH": "/tmp/test-cache"}
    first = network_manager.scan_wifi_networks(config)
    second = network_manager.scan_wifi_networks(config)

    assert first == second
    assert calls["count"] == 1



def test_scan_wifi_networks_force_refresh_bypasses_cache(monkeypatch):
    calls = {"count": 0}
    sample_output = "*:PlantWiFi:84:WPA2\n:Guest:48:Open\n"

    monkeypatch.setattr(network_manager, "_rescan_wifi", lambda config, interface: None)

    def fake_run(config, arguments):
        calls["count"] += 1
        return sample_output

    monkeypatch.setattr(network_manager, "_run_nmcli", fake_run)

    config = {"WIFI_INTERFACE": "wlan0", "WIFI_SCAN_CACHE_SECONDS": 30, "REPO_PATH": "/tmp/test-force-refresh"}
    network_manager.scan_wifi_networks(config)
    network_manager.scan_wifi_networks(config, force_refresh=True)

    assert calls["count"] == 2



def test_scan_wifi_networks_does_not_cache_empty_results(monkeypatch):
    calls = {"count": 0}

    monkeypatch.setattr(network_manager, "_rescan_wifi", lambda config, interface: None)

    def fake_run(config, arguments):
        calls["count"] += 1
        return ""

    monkeypatch.setattr(network_manager, "_run_nmcli", fake_run)

    config = {"WIFI_INTERFACE": "wlan0", "WIFI_SCAN_CACHE_SECONDS": 30, "REPO_PATH": "/tmp/test-empty-cache"}
    first = network_manager.scan_wifi_networks(config)
    second = network_manager.scan_wifi_networks(config)

    assert first == []
    assert second == []
    assert calls["count"] == 4



def test_get_dashboard_state_uses_short_cache(monkeypatch):
    calls = {"devices": 0, "wifi": 0, "internet": 0}

    def fake_devices(config):
        calls["devices"] += 1
        return [{"device": "wlan0"}]

    def fake_wifi(config):
        calls["wifi"] += 1
        return [{"ssid": "PlantWiFi", "signal": "80", "security": "WPA2", "in_use": True}]

    def fake_internet(config):
        calls["internet"] += 1
        return True

    monkeypatch.setattr(network_manager, "_get_device_status", fake_devices)
    monkeypatch.setattr(network_manager, "scan_wifi_networks", fake_wifi)
    monkeypatch.setattr(network_manager, "has_internet_access", fake_internet)

    config = {"STATUS_CACHE_SECONDS": 10, "REPO_PATH": "/tmp/test-dashboard-cache"}
    first = network_manager.get_dashboard_state(config)
    second = network_manager.get_dashboard_state(config)

    assert first["internet_access"] is True
    assert second["wifi_networks"][0]["ssid"] == "PlantWiFi"
    assert calls == {"devices": 1, "wifi": 1, "internet": 1}


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


def test_list_connection_profiles_accepts_short_type_names(monkeypatch):
    """NM 1.42+ outputs short type names like 'ethernet' instead of '802-3-ethernet'."""
    sample_output = "wifi profile:wifi:wlan0\nnetplan-eth0:ethernet:eth0\nSpare LAN:ethernet:--\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    profiles = network_manager.list_connection_profiles(
        {"ETHERNET_INTERFACE": "eth0"},
        connection_type=network_manager.ETHERNET_CONNECTION_TYPE,
        interface_name="eth0",
    )

    assert [profile["name"] for profile in profiles] == ["netplan-eth0", "Spare LAN"]
    assert profiles[0]["active"] is True


def test_get_active_ethernet_connection_returns_matching_profile(monkeypatch):
    sample_output = "Office WiFi:wlan0:802-11-wireless\nWired connection 1:eth0:802-3-ethernet\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    active = network_manager.get_active_ethernet_connection({})

    assert active == {"name": "Wired connection 1", "device": "eth0", "type": "802-3-ethernet"}


def test_get_active_ethernet_connection_accepts_short_type_name(monkeypatch):
    """NM 1.42+ may return 'ethernet' instead of '802-3-ethernet' in active connection output."""
    sample_output = "Office WiFi:wlan0:wifi\nnetplan-eth0:eth0:ethernet\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    active = network_manager.get_active_ethernet_connection({})

    assert active is not None
    assert active["name"] == "netplan-eth0"


def test_get_active_ethernet_connection_finds_profile_on_eth1(monkeypatch):
    """Profile connected to eth1 should be found even if ETHERNET_INTERFACE=eth0."""
    sample_output = "Office WiFi:wlan0:wifi\nnetplan-eth0:eth1:ethernet\n"

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: sample_output)

    active = network_manager.get_active_ethernet_connection({})

    assert active is not None
    assert active["name"] == "netplan-eth0"
    assert active["device"] == "eth1"


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

    # Two separate modify calls: first sets address/dns/never-default,
    # second sets gateway in isolation (avoids NM 1.52 silent-drop bug)
    assert len(calls) == 2
    assert calls[0] == [
        "connection", "modify", "Wired connection 1",
        "ipv4.method", "manual",
        "ipv4.addresses", "192.168.50.20/24",
        "ipv4.dns", "8.8.8.8,1.1.1.1",
        "ipv4.never-default", "no",
        "ipv6.never-default", "no",
    ]
    assert calls[1] == [
        "connection", "modify", "Wired connection 1",
        "ipv4.gateway", "192.168.50.1",
    ]


def test_set_connection_never_default_uses_nmcli_modify(monkeypatch):
    calls = []

    monkeypatch.setattr(network_manager, "_run_nmcli", lambda config, arguments: calls.append(arguments) or "")

    network_manager.set_connection_never_default({}, "Wired connection 1", enabled=True)

    assert calls == [[
        "connection",
        "modify",
        "Wired connection 1",
        "ipv4.never-default",
        "yes",
        "ipv6.never-default",
        "yes",
    ]]


def test_build_nmcli_command_uses_sudo_for_mutating_commands():
    command = network_manager._build_nmcli_command(
        {"NMCLI_BIN": "nmcli", "USE_SUDO_FOR_NMCLI": True},
        ["device", "wifi", "connect", "PlantWiFi"],
    )

    assert command[:3] == ["sudo", "-n", "nmcli"]


def test_build_nmcli_command_uses_sudo_for_connection_add():
    command = network_manager._build_nmcli_command(
        {"NMCLI_BIN": "nmcli", "USE_SUDO_FOR_NMCLI": True},
        ["connection", "add", "type", "wifi", "ifname", "wlan0", "con-name", "Staff2019", "ssid", "Staff2019"],
    )

    assert command[:3] == ["sudo", "-n", "nmcli"]


def test_build_nmcli_command_skips_sudo_for_read_only_commands():
    command = network_manager._build_nmcli_command(
        {"NMCLI_BIN": "nmcli", "USE_SUDO_FOR_NMCLI": True},
        ["device", "wifi", "list"],
    )

    assert command == ["nmcli", "device", "wifi", "list"]
