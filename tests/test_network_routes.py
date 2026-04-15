from app.routes import network as network_routes


def _login(client):
    response = client.post("/login", data={"password": "secret123"}, follow_redirects=False)
    assert response.status_code == 302


def test_wifi_page_shows_explicit_scan_and_select_flow(client, monkeypatch):
    wifi_networks = [{"ssid": "PlantWiFi", "signal": "81", "security": "WPA2", "in_use": False}]

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config: wifi_networks)
    monkeypatch.setattr(network_routes, "get_dashboard_state", lambda config: {"hostname": "pi", "interfaces": [], "wifi_networks": wifi_networks})

    _login(client)
    response = client.get("/wifi")

    assert response.status_code == 200
    assert b"Scan Available Networks" in response.data
    assert b"Use This Network" in response.data
    assert b"PlantWiFi" in response.data


def test_wifi_page_prefills_selected_network_from_query(client, monkeypatch):
    wifi_networks = [{"ssid": "CabinetWiFi", "signal": "75", "security": "WPA2", "in_use": False}]

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config: wifi_networks)
    monkeypatch.setattr(network_routes, "get_dashboard_state", lambda config: {"hostname": "pi", "interfaces": [], "wifi_networks": wifi_networks})

    _login(client)
    response = client.get("/wifi?ssid=CabinetWiFi")

    assert response.status_code == 200
    assert b'value="CabinetWiFi"' in response.data
