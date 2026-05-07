from app.routes import network as network_routes


def _login(client):
    response = client.post("/login", data={"password": "secret123"}, follow_redirects=False)
    assert response.status_code == 302


def test_wifi_page_shows_explicit_scan_and_connect_flow(client, monkeypatch):
    wifi_networks = [{"ssid": "PlantWiFi", "signal": "81", "security": "WPA2", "in_use": False}]
    scan_calls = []

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config, force_refresh=False: scan_calls.append(force_refresh) or wifi_networks)
    monkeypatch.setattr(network_routes, "get_saved_wifi_ssids", lambda config: set())
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
    assert scan_calls == [True]


def test_wifi_page_prefills_selected_network_from_query(client, monkeypatch):
    wifi_networks = [{"ssid": "CabinetWiFi", "signal": "75", "security": "WPA2", "in_use": False}]

    monkeypatch.setattr(network_routes, "scan_wifi_networks", lambda config, force_refresh=False: wifi_networks)
    monkeypatch.setattr(network_routes, "get_saved_wifi_ssids", lambda config: set())
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
    assert b"Logger health" in response.data
    assert b"Open Portainer" in response.data
    assert b"Checking status" in response.data
    assert b"MQTT logger" in response.data
    assert b"PLC logger" in response.data
    assert b"Queue backlog" in response.data
    assert b"OpsViewer" in response.data
    assert b"Portainer" in response.data
    assert b":9443" in response.data


def test_datalogger_page_hides_plc_card_when_mode_is_mqtt(client, monkeypatch):
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
    monkeypatch.setattr(
        network_routes,
        "_build_initial_datalogger_status",
        lambda config, host: {
            "mqtt_logger": {},
            "plc_logger": {},
            "warnings": [],
            "system_status_label": "Checking status",
            "system_status_class": "status-neutral",
            "system_status_detail": "Waiting",
            "mqtt_ui_url": "",
            "portainer_running": False,
            "portainer_url": "",
        },
    )
    monkeypatch.setattr(network_routes, "get_logger_mode", lambda config: "mqtt")

    _login(client)
    response = client.get("/datalogger")

    assert response.status_code == 200
    assert b"MQTT logger" in response.data
    assert b'id="plc-card-title">' not in response.data


def test_system_post_can_set_logger_mode(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "set_logger_mode",
        lambda config, mode: calls.append(mode) or {"success": True, "message": "Logger mode set."},
    )
    monkeypatch.setattr(network_routes, "get_system_summary", lambda config: {
        "hostname": "ess-pi",
        "disk_total": "1.0 GB",
        "disk_used": "0.5 GB",
        "disk_free": "0.5 GB",
        "disk_percent": 50,
    })
    monkeypatch.setattr(network_routes, "get_update_status", lambda config: {
        "current_branch": "main",
        "current_commit": "abc123",
        "update_available": False,
        "behind_by": 0,
        "error": "",
        "state": "idle",
        "message": "",
        "log_excerpt": "",
    })
    monkeypatch.setattr(network_routes, "get_datalogger_status", lambda config, host=None: {
        "docker_available": True,
        "portainer_running": False,
        "portainer_installed": False,
        "portainer_url": "",
        "logger_mode": "auto",
    })

    _login(client)
    response = client.post("/system", data={"action": "set_logger_mode", "logger_mode": "mqtt"}, follow_redirects=False)

    assert response.status_code == 302
    assert calls == ["mqtt"]


def test_datalogger_page_renders_without_live_status_probe(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_datalogger_status",
        lambda config, host=None: (_ for _ in ()).throw(AssertionError("live status should not be fetched during page render")),
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
    assert b"Checking status" in response.data
    assert b"PlantWiFi" in response.data


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


def test_wifi_post_can_restart_network_manager(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        network_routes,
        "restart_network_manager",
        lambda config: calls.append(True) or {"success": True, "message": "NetworkManager restarted."},
    )

    _login(client)
    response = client.post("/wifi", data={"action": "restart_network_manager"}, follow_redirects=False)

    assert response.status_code == 302
    assert calls == [True]


def test_wifi_post_rejects_blank_password_for_secured_unsaved_network(client, monkeypatch):
    apply_calls = []
    monkeypatch.setattr(network_routes, "get_saved_wifi_ssids", lambda config: set())
    monkeypatch.setattr(
        network_routes,
        "apply_wifi_settings",
        lambda config, ssid, password, hidden: apply_calls.append((ssid, password, hidden)) or {"success": True, "message": "ok"},
    )

    _login(client)
    response = client.post(
        "/wifi",
        data={"ssid": "Staff2019", "security": "WPA2", "password": ""},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert apply_calls == []


def test_wifi_post_allows_blank_password_for_secured_saved_network(client, monkeypatch):
    apply_calls = []
    monkeypatch.setattr(network_routes, "get_saved_wifi_ssids", lambda config: {"Staff2019"})
    monkeypatch.setattr(
        network_routes,
        "apply_wifi_settings",
        lambda config, ssid, password, hidden: apply_calls.append((ssid, password, hidden)) or {"success": True, "message": "ok"},
    )

    _login(client)
    response = client.post(
        "/wifi",
        data={"ssid": "Staff2019", "security": "WPA2", "password": ""},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert apply_calls == [("Staff2019", "", False)]


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
            "json_files": [
                {"id": "logger-json", "label": "Logger JSON", "path": "/tmp/logger.json", "editor_type": "json"},
                {"id": "app-env", "label": "app.env", "path": "/etc/pi-network-admin/app.env", "editor_type": "text"},
            ],
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
        lambda config, refresh=False: {"current_branch": "main", "current_commit": "abc1234", "update_available": True, "behind_by": 2, "error": ""},
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
        lambda config, refresh=False: {"current_branch": "main", "current_commit": "abc1234", "update_available": False, "behind_by": 0, "error": ""},
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
        lambda config, refresh=False: {"current_branch": "main", "current_commit": "abc1234", "update_available": False, "behind_by": 0, "error": ""},
    )

    _login(client)
    response = client.post("/system", data={"action": "update"}, follow_redirects=False)

    assert response.status_code == 302
    assert update_calls == [True]


def test_system_check_updates_redirects_with_refresh_flag(client, monkeypatch):
    monkeypatch.setattr(
        network_routes,
        "get_update_status",
        lambda config, refresh=False: {
            "current_branch": "main",
            "current_commit": "abc1234",
            "update_available": True,
            "behind_by": 1,
            "error": "",
        },
    )
    monkeypatch.setattr(
        network_routes,
        "get_system_summary",
        lambda config: {"hostname": "ess-pi", "disk_total": "64 GB", "disk_used": "18 GB", "disk_free": "46 GB", "disk_percent": 28},
    )

    _login(client)
    response = client.post("/system", data={"action": "check_updates"}, follow_redirects=False)

    assert response.status_code == 302
    assert "/system?refresh=1" in response.headers["Location"]
