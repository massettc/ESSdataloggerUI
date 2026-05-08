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


def test_apply_wifi_settings_recovers_from_missing_key_mgmt_when_password_supplied(monkeypatch):
    connect_attempts = []
    deleted_profiles = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})

    def flaky_connect(config, ssid, password, hidden):
        connect_attempts.append((ssid, password, hidden))
        if len(connect_attempts) == 1:
            raise network_apply.NetworkManagerError("802-11-wireless-security.key-mgmt: property is missing")

    monkeypatch.setattr(network_apply, "connect_wifi", flaky_connect)
    monkeypatch.setattr(
        network_apply,
        "list_connection_profiles",
        lambda config, connection_type=None, interface_name=None: [{"name": "Staff2019", "type": "wifi", "device": "", "active": False}],
    )
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Staff2019"])
    monkeypatch.setattr(network_apply, "get_connection_wifi_ssid", lambda config, connection_name: "Staff2019")
    monkeypatch.setattr(network_apply, "delete_connection_profile", lambda config, name: deleted_profiles.append(name))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Staff2019",
        "goodpass",
        False,
    )

    assert result["success"] is True
    assert len(connect_attempts) == 2
    assert deleted_profiles == ["Staff2019"]


def test_apply_wifi_settings_missing_key_mgmt_without_password_prompts_for_password(monkeypatch):
    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: None)

    def fail_connect(config, ssid, password, hidden):
        raise network_apply.NetworkManagerError("802-11-wireless-security.key-mgmt: property is missing")

    monkeypatch.setattr(network_apply, "connect_wifi", fail_connect)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Staff2019",
        "",
        False,
    )

    assert result["success"] is False
    assert "Enter the Wi-Fi password" in result["message"]


def test_apply_wifi_settings_rebuilds_profile_if_retry_still_has_key_mgmt(monkeypatch):
    connect_attempts = []
    deleted_profiles = []
    rebuilt = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})

    def always_bad_connect(config, ssid, password, hidden):
        connect_attempts.append((ssid, password, hidden))
        raise network_apply.NetworkManagerError("802-11-wireless-security.key-mgmt: property is missing")

    monkeypatch.setattr(network_apply, "connect_wifi", always_bad_connect)
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Staff2019"])
    monkeypatch.setattr(
        network_apply,
        "list_connection_profiles",
        lambda config, connection_type=None, interface_name=None: [{"name": "Staff2019", "type": "wifi", "device": "", "active": False}],
    )
    monkeypatch.setattr(network_apply, "get_connection_wifi_ssid", lambda config, connection_name: "Staff2019")
    monkeypatch.setattr(network_apply, "delete_connection_profile", lambda config, name: deleted_profiles.append(name))
    monkeypatch.setattr(
        network_apply,
        "_rebuild_wifi_profile_and_connect",
        lambda config, ssid, password, hidden: rebuilt.append((ssid, password, hidden)),
    )
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01, "WIFI_INTERFACE": "wlan0"},
        "Staff2019",
        "goodpass",
        False,
    )

    assert result["success"] is True
    assert len(connect_attempts) == 2
    assert deleted_profiles == ["Staff2019"]
    assert rebuilt == [("Staff2019", "goodpass", False)]


def test_apply_wifi_settings_retries_after_stale_profile_secret_error(monkeypatch):
    connect_attempts = []
    deleted_profiles = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "OldWiFi", "device": "wlan0"})

    def flaky_connect(config, ssid, password, hidden):
        connect_attempts.append((ssid, password, hidden))
        if len(connect_attempts) == 1:
            raise network_apply.NetworkManagerError("Secrets were required, but not provided.")

    monkeypatch.setattr(network_apply, "connect_wifi", flaky_connect)
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Unit 81 Starlink"])
    monkeypatch.setattr(
        network_apply,
        "list_connection_profiles",
        lambda config, connection_type=None, interface_name=None: [{"name": "Unit 81 Starlink", "type": "wifi", "device": "", "active": False}],
    )
    monkeypatch.setattr(network_apply, "get_connection_wifi_ssid", lambda config, connection_name: "Unit 81 Starlink")
    monkeypatch.setattr(network_apply, "delete_connection_profile", lambda config, name: deleted_profiles.append(name))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Unit 81 Starlink",
        "correctpass",
        False,
    )

    assert result["success"] is True
    assert len(connect_attempts) == 2
    assert deleted_profiles == ["Unit 81 Starlink"]


def test_apply_wifi_settings_falls_back_to_saved_profile_when_ssid_lookup_fails(monkeypatch):
    bring_up_calls = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "ESS", "device": "wlan0"})

    def fail_connect(config, ssid, password, hidden):
        raise network_apply.NetworkManagerError("No network with SSID 'Unit 81 Starlink' found.")

    monkeypatch.setattr(network_apply, "connect_wifi", fail_connect)
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Unit 81 Starlink"])
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: bring_up_calls.append(name))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Unit 81 Starlink",
        "",
        False,
    )

    assert result["success"] is True
    assert bring_up_calls == ["Unit 81 Starlink"]


def test_apply_wifi_settings_prompts_for_password_when_saved_profile_needs_secrets(monkeypatch):
    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "ESS", "device": "wlan0"})

    def fail_connect(config, ssid, password, hidden):
        raise network_apply.NetworkManagerError("Connection activation failed: Secrets were required, but not provided.")

    monkeypatch.setattr(network_apply, "connect_wifi", fail_connect)
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Unit 81 Starlink"])
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: None)

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Unit 81 Starlink",
        "",
        False,
    )

    assert result["success"] is False
    assert "needs a password" in result["message"]


def test_apply_wifi_settings_uses_saved_profile_first_when_password_blank(monkeypatch):
    connect_calls = []
    bring_up_calls = []

    monkeypatch.setattr(network_apply, "get_active_wifi_connection", lambda config: {"name": "ESS", "device": "wlan0"})
    monkeypatch.setattr(network_apply, "find_wifi_profile_names_for_ssid", lambda config, ssid: ["Unit 81 Starlink"])
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: bring_up_calls.append(name))
    monkeypatch.setattr(network_apply, "_verify_wifi_connection", lambda config, expected_ssid: True)
    monkeypatch.setattr(
        network_apply,
        "connect_wifi",
        lambda config, ssid, password, hidden: connect_calls.append((ssid, password, hidden)),
    )

    result = network_apply.apply_wifi_settings(
        {"VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        "Unit 81 Starlink",
        "",
        False,
    )

    assert result["success"] is True
    assert bring_up_calls == ["Unit 81 Starlink"]
    assert connect_calls == []


def test_apply_ethernet_settings_reconnects_device(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "connect_device", lambda config, interface_name: calls.append(("connect", interface_name)))
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: True)

    result = network_apply.apply_ethernet_settings({"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01})

    assert result["success"] is True
    assert calls == [("connect", "eth0")]


def test_apply_ethernet_settings_marks_ethernet_non_default_when_wifi_preferred(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "set_connection_never_default", lambda config, connection_name, enabled: calls.append((connection_name, enabled)))
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: None)
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: True)

    result = network_apply.apply_ethernet_settings(
        {"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01, "PREFER_WLAN_FOR_INTERNET": True},
        connection_name="Wired connection 1",
    )

    assert result["success"] is True
    assert calls == [("Wired connection 1", True)]


def test_apply_ethernet_settings_keeps_default_route_when_manual_gateway_is_set(monkeypatch):
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: {"name": "Wired connection 1", "device": "eth0"})
    monkeypatch.setattr(network_apply, "get_connection_ipv4_config", lambda config, connection_name: {
        "method": "auto",
        "address": "",
        "prefix": "",
        "gateway": "",
        "dns": "",
    })
    monkeypatch.setattr(network_apply, "set_connection_ipv4_config", lambda config, **kwargs: None)
    monkeypatch.setattr(network_apply, "set_connection_autoconnect", lambda config, name, enabled: None)
    monkeypatch.setattr(network_apply, "persist_connection_to_etc", lambda config, name: None)
    monkeypatch.setattr(network_apply, "set_connection_never_default", lambda config, connection_name, enabled: calls.append((connection_name, enabled)))
    monkeypatch.setattr(network_apply, "bring_up_connection", lambda config, name: None)
    monkeypatch.setattr(network_apply, "_verify_connection", lambda config, interface_name, connection_type, expected_name=None: True)

    result = network_apply.apply_ethernet_settings(
        {"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01, "PREFER_WLAN_FOR_INTERNET": True},
        connection_name="Wired connection 1",
        ip_method="manual",
        ip_address="192.168.0.11",
        ip_prefix="24",
        gateway="192.168.0.1",
        dns="8.8.8.8",
    )

    assert result["success"] is True
    assert calls == []


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


def test_apply_ethernet_settings_saves_config_even_when_verify_times_out(monkeypatch):
    """When config is saved but the connection doesn't verify in time, settings are kept
    (not rolled back) and success=True is returned so the gateway persists."""
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
    monkeypatch.setattr(network_apply, "set_connection_autoconnect", lambda config, name, enabled: None)
    monkeypatch.setattr(network_apply, "persist_connection_to_etc", lambda config, name: None)
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

    # Config was saved — should NOT be rolled back, should return success=True
    assert result["success"] is True
    modify_calls = [c for c in calls if c[0] == "modify"]
    assert len(modify_calls) == 1, "Should only have one modify call (no restore)"
    assert modify_calls[0][1]["gateway"] == "192.168.2.1"


def test_apply_ethernet_settings_saves_config_when_device_unavailable(monkeypatch):
    """When bring_up_connection fails (e.g. eth0 unavailable/no cable), settings should be
    saved to the profile and NOT rolled back. Returns success=True with an informative message."""
    calls = []

    monkeypatch.setattr(network_apply, "get_active_ethernet_connection", lambda config: None)
    monkeypatch.setattr(network_apply, "get_connection_ipv4_config", lambda config, connection_name: {
        "method": "auto",
        "address": "",
        "prefix": "",
        "gateway": "",
        "dns": "",
    })
    monkeypatch.setattr(network_apply, "set_connection_ipv4_config", lambda config, **kwargs: calls.append(("modify", kwargs)))
    monkeypatch.setattr(network_apply, "set_connection_autoconnect", lambda config, name, enabled: None)
    monkeypatch.setattr(network_apply, "persist_connection_to_etc", lambda config, name: None)

    def fail_bring_up(config, name):
        raise network_apply.NetworkManagerError("Error: Connection activation failed: device not available.")

    monkeypatch.setattr(network_apply, "bring_up_connection", fail_bring_up)

    result = network_apply.apply_ethernet_settings(
        {"ETHERNET_INTERFACE": "eth0", "VERIFY_TIMEOUT_SECONDS": 1, "VERIFY_POLL_SECONDS": 0.01},
        connection_name="netplan-eth0",
        ip_method="manual",
        ip_address="192.168.0.11",
        ip_prefix="24",
        gateway="192.168.0.1",
        dns="8.8.8.8",
    )

    # Config should have been saved once (not rolled back)
    assert len([c for c in calls if c[0] == "modify"]) == 1
    assert calls[0][1]["gateway"] == "192.168.0.1"
    assert result["success"] is True
    assert "saved" in result["message"].lower()
    assert "unavailable" in result["message"].lower() or "cable" in result["message"].lower()
