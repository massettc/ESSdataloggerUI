from app.routes import network as network_routes


def _login(client):
    response = client.post("/login", data={"password": "secret123"}, follow_redirects=False)
    assert response.status_code == 302


def test_wifi_page_shows_explicit_scan_and_connect_flow(client, monkeypatch):
    wifi_networks = [{"ssid": "PlantWiFi", "signal": "81", "security": "WPA2", "in_use": False}]

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config: wifi_networks)
    monkeypatch.setattr(
        network_routes,
        "get_dashboard_state",
        lambda config: {"hostname": "pi", "interfaces": [], "wifi_networks": wifi_networks, "internet_access": True},
    )

    _login(client)
    response = client.get("/wifi")

    assert response.status_code == 200
    assert b"Scan Available Networks" in response.data
    assert b"Internet access" in response.data
    assert b"Connect" in response.data
    assert b"PlantWiFi" in response.data


def test_wifi_page_prefills_selected_network_from_query(client, monkeypatch):
    wifi_networks = [{"ssid": "CabinetWiFi", "signal": "75", "security": "WPA2", "in_use": False}]

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config: wifi_networks)
    monkeypatch.setattr(
        network_routes,
        "get_dashboard_state",
        lambda config: {"hostname": "pi", "interfaces": [], "wifi_networks": wifi_networks, "internet_access": False},
    )

    _login(client)
    response = client.get("/wifi?ssid=CabinetWiFi")

    assert response.status_code == 200
    assert b'value="CabinetWiFi"' in response.data
    assert b"Offline" in response.data


def test_sidebar_includes_datalogger_and_system_links(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_dashboard_state",
        lambda config: {"hostname": "pi", "interfaces": [], "wifi_networks": [], "internet_access": True},
    )

    _login(client)
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"Datalogger" in response.data
    assert b"System" in response.data


def test_datalogger_page_renders_placeholder(client):
    _login(client)
    response = client.get("/datalogger")

    assert response.status_code == 200
    assert b"Datalogger" in response.data
    assert b"Coming soon" in response.data


def test_system_page_shows_hostname_disk_and_updates(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_system_summary",
        lambda config: {"hostname": "ess-pi", "disk_total": "64 GB", "disk_used": "18 GB", "disk_free": "46 GB", "disk_percent": 28},
    )
    monkeypatch.setattr(
        network_routes,
        "get_update_status",
        lambda config: {"current_branch": "main", "current_commit": "abc1234", "update_available": True, "behind_by": 2, "error": ""},
    )

    _login(client)
    response = client.get("/system")

    assert response.status_code == 200
    assert b"ess-pi" in response.data
    assert b"64 GB" in response.data
    assert b"Check for updates" in response.data
    assert b"Update available" in response.data


def test_system_hostname_post_calls_update(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "set_system_hostname",
        lambda config, hostname: calls.append(hostname) or {"success": True, "message": "Hostname updated."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_system_summary",
        lambda config: {"hostname": "ess-pi", "disk_total": "64 GB", "disk_used": "18 GB", "disk_free": "46 GB", "disk_percent": 28},
    )
    monkeypatch.setattr(
        network_routes,
        "get_update_status",
        lambda config: {"current_branch": "main", "current_commit": "abc1234", "update_available": False, "behind_by": 0, "error": ""},
    )

    _login(client)
    response = client.post("/system", data={"action": "hostname", "hostname": "ess-new"}, follow_redirects=False)

    assert response.status_code == 302
    assert calls == ["ess-new"]


def test_system_update_post_runs_update(client, monkeypatch):
    update_calls = []
    monkeypatch.setattr(
        network_routes,
        "run_system_update",
        lambda config: update_calls.append(True) or {"success": True, "message": "Update installed."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_system_summary",
        lambda config: {"hostname": "ess-pi", "disk_total": "64 GB", "disk_used": "18 GB", "disk_free": "46 GB", "disk_percent": 28},
    )
    monkeypatch.setattr(
        network_routes,
        "get_update_status",
        lambda config: {"current_branch": "main", "current_commit": "abc1234", "update_available": False, "behind_by": 0, "error": ""},
    )

    _login(client)
    response = client.post("/system", data={"action": "update"}, follow_redirects=False)

    assert response.status_code == 302
    assert update_calls == [True]
