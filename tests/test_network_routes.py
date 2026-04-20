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


def test_sidebar_prioritizes_datalogger_and_hides_dashboard_link(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_datalogger_status",
        lambda config, host=None: {
            "docker_available": True,
            "docker_running": True,
            "portainer_installed": True,
            "portainer_running": True,
            "portainer_url": "http://ess-pi:9000",
            "mqtt_ui_url": "http://ess-pi:8080",
            "active_logger": "MQTT Logger",
            "warnings": [],
            "mqtt_logger": {},
            "plc_logger": {},
            "containers": [],
            "error": "",
        },
    )
    monkeypatch.setattr(
        network_routes,
        "get_dashboard_state",
        lambda config: {
            "hostname": "ess-pi",
            "interfaces": [{"device": "wlan0", "type": "wifi", "state": "connected", "connection": "PlantWiFi"}],
            "wifi_networks": [],
            "internet_access": True,
        },
    )

    _login(client)
    response = client.get("/datalogger")

    assert response.status_code == 200
    assert b"Datalogger" in response.data
    assert b"System" in response.data
    assert b"Tech tools" in response.data
    assert b'href="/dashboard"' not in response.data
    assert b"<h1>ess-pi</h1>" in response.data
    assert b"Internet status" in response.data
    assert b"WiFi status" in response.data
    assert b"PlantWiFi" in response.data


def test_datalogger_page_shows_portainer_status(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_datalogger_status",
        lambda config, host=None: {
            "docker_available": True,
            "docker_running": True,
            "portainer_installed": True,
            "portainer_running": True,
            "portainer_url": "http://ess-pi:9000",
            "mqtt_ui_url": "http://ess-pi:8080",
            "active_logger": "MQTT Logger",
            "warnings": ["Cloud backlog: 1089 buffered"],
            "system_status_label": "OpsViewer backlog",
            "system_status_class": "status-warning",
            "system_status_detail": "MQTT logger has 1089 records buffered for OpsViewer.",
            "mqtt_logger": {
                "name": "opsviewer2-edge",
                "summary": "PLC connected; 1089 records buffered for OpsViewer",
                "last_activity_text": "2026-04-16T17:39:10Z",
                "last_push_age_seconds": 3,
                "last_push_label": "Last pushed 3 sec ago",
                "status_class": "status-warning",
                "plc_link_label": "Connected",
                "plc_link_class": "status-online",
                "opsviewer_link_label": "Backlog",
                "opsviewer_link_class": "status-warning",
                "device_id": "ESS-UNIT-81",
                "channel_count": 10,
                "queue_size": 1089,
                "broker_clients_connected": 7,
                "error": "",
            },
            "plc_logger": {
                "name": "plcreader",
                "summary": "Connected to PLC and sending to OpsViewer (43 measurements)",
                "last_activity_text": "04/16/2026 17:36:03",
                "last_push_age_seconds": 12,
                "last_push_label": "Last pushed 12 sec ago",
                "status_class": "status-online",
                "plc_link_label": "Connected",
                "plc_link_class": "status-online",
                "opsviewer_link_label": "Connected",
                "opsviewer_link_class": "status-online",
                "measurements": 43,
                "queue_size": 0,
                "error": "",
            },
            "containers": [{"name": "logger", "image": "my/logger:latest", "status": "Up 2 hours"}],
            "error": "",
        },
    )

    _login(client)
    response = client.get("/datalogger")

    assert response.status_code == 200
    assert b"Logger health" in response.data
    assert b"Open MQTT UI" in response.data
    assert b"OpsViewer backlog" in response.data
    assert b"MQTT logger" in response.data
    assert b"PLC logger" in response.data
    assert b"PLC connected; 1089 records buffered for OpsViewer" in response.data
    assert b"Connected to PLC and sending to OpsViewer (43 measurements)" in response.data
    assert b"Queue backlog" in response.data
    assert b"1089 buffered" in response.data
    assert b"Last pushed 3 sec ago" in response.data
    assert b"OpsViewer" in response.data
    assert b"Portainer" in response.data
    assert b"http://ess-pi:9000" in response.data
    assert b"logger" in response.data


def test_datalogger_post_can_start_portainer(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "ensure_portainer",
        lambda config: calls.append(True) or {"success": True, "message": "Portainer is ready."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_datalogger_status",
        lambda config, host=None: {
            "docker_available": True,
            "docker_running": True,
            "portainer_installed": True,
            "portainer_running": True,
            "portainer_url": "http://ess-pi:9000",
            "containers": [],
            "error": "",
        },
    )

    _login(client)
    response = client.post("/datalogger", data={"action": "portainer"}, follow_redirects=False)

    assert response.status_code == 302
    assert calls == [True]


def test_datalogger_page_handles_unexpected_status_errors(client, monkeypatch):
    monkeypatch.setattr(network_routes, "get_datalogger_status", lambda config, host=None: (_ for _ in ()).throw(RuntimeError("boom")))

    _login(client)
    response = client.get("/datalogger")

    assert response.status_code == 200
    assert b"Datalogger" in response.data


def test_datalogger_status_api_handles_unexpected_errors(client, monkeypatch):
    monkeypatch.setattr(network_routes, "get_datalogger_status", lambda config, host=None: (_ for _ in ()).throw(RuntimeError("boom")))

    _login(client)
    response = client.get("/datalogger/status")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json()["error"] == "boom"


def test_datalogger_status_api_includes_live_connectivity(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_datalogger_status",
        lambda config, host=None: {"mqtt_logger": {}, "plc_logger": {}, "warnings": [], "error": ""},
    )
    monkeypatch.setattr(
        network_routes,
        "get_dashboard_state",
        lambda config: {
            "hostname": "ess-pi",
            "interfaces": [{"device": "wlan0", "state": "connected", "connection": "PlantWiFi"}],
            "wifi_networks": [],
            "internet_access": True,
        },
    )

    _login(client)
    response = client.get("/datalogger/status")

    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["connectivity"]["internet_label"] == "Online"
    assert payload["connectivity"]["wifi_label"] == "PlantWiFi"


def test_technician_tools_page_shows_buttons_terminal_and_json_editor(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_technician_tools_state",
        lambda config: {
            "commands": [
                {
                    "id": "show-date",
                    "label": "Show date",
                    "command": "date",
                    "description": "Display current date",
                    "confirm": False,
                    "builtin": True,
                }
            ],
            "last_result": {
                "command_label": "Show date",
                "command": "date",
                "exit_code": 0,
                "output": "Thu Apr 16",
            },
            "json_files": [{"id": "logger-json", "label": "Logger JSON", "path": "/tmp/logger.json"}],
            "selected_json_file": "logger-json",
            "json_editor_content": '{\n  "enabled": true\n}',
            "json_editor_error": "",
            "error": "",
        },
    )

    _login(client)
    response = client.get("/tools")

    assert response.status_code == 200
    assert b"Technician Tools" in response.data
    assert b"Add new button" in response.data
    assert b"Show date" in response.data
    assert b"Terminal output" in response.data
    assert b"JSON editor" in response.data
    assert b"Logger JSON" in response.data
    assert b'"enabled": true' in response.data
    assert b"Remove" in response.data



def test_technician_tools_post_can_add_button(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "add_technician_command",
        lambda config, label, command, description="", confirm=False: calls.append((label, command, description, confirm))
        or {"success": True, "message": "Button saved."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_technician_tools_state",
        lambda config: {"commands": [], "last_result": None, "error": ""},
    )

    _login(client)
    response = client.post(
        "/tools",
        data={
            "action": "add_command",
            "label": "Check disk",
            "command": "df -h",
            "description": "Disk usage",
            "confirm": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert calls == [("Check disk", "df -h", "Disk usage", True)]



def test_technician_tools_post_can_run_saved_button(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "start_technician_command",
        lambda config, command_id: calls.append(command_id) or {"success": True, "message": "Started command."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_technician_tools_state",
        lambda config: {"commands": [], "last_result": None, "error": ""},
    )

    _login(client)
    response = client.post("/tools", data={"action": "run_command", "command_id": "show-date"}, follow_redirects=False)

    assert response.status_code == 302
    assert calls == ["show-date"]



def test_technician_tools_post_can_save_json_file(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "save_technician_json_file",
        lambda config, file_id, content: calls.append((file_id, content)) or {"success": True, "message": "JSON saved."},
    )
    monkeypatch.setattr(
        network_routes,
        "get_technician_tools_state",
        lambda config: {"commands": [], "last_result": None, "json_files": [], "json_editor_content": "", "json_editor_error": "", "error": ""},
    )

    _login(client)
    response = client.post(
        "/tools",
        data={"action": "save_json", "json_file": "logger-json", "json_content": '{"enabled": true}'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert calls == [("logger-json", '{"enabled": true}')]



def test_technician_tools_status_api_returns_live_terminal_state(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_technician_tools_state",
        lambda config: {
            "commands": [],
            "last_result": {
                "command_label": "Download plcreader",
                "command": "docker pull sample",
                "status": "running",
                "exit_code": None,
                "output": "Pulling fs layer",
                "ran_at": "2026-04-16 17:30:00",
            },
            "json_files": [],
            "json_editor_content": "",
            "json_editor_error": "",
            "error": "",
        },
    )

    _login(client)
    response = client.get("/tools/status")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json()["last_result"]["status"] == "running"



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
