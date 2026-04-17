import subprocess

from app.services import datalogger_manager


def test_get_datalogger_status_parses_logger_roles_and_health(monkeypatch):
    queue_json = '{"Length": 7}'

    outputs = {
        ("docker", "version", "--format", "{{.Server.Version}}"):
            subprocess.CompletedProcess(args=[], returncode=0, stdout="24.0\n", stderr=""),
        ("docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "opsviewer2-edge|opsviewer2/edge:r4|Up 20 minutes\n"
                    "portainer|portainer/portainer-ce:lts|Up 20 minutes\n"
                    "plcreader|opsviewer2/ultralight:r1363|Exited (132) About an hour ago\n"
                ),
                stderr="",
            ),
        ("docker", "logs", "--tail", "50", "opsviewer2-edge"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    'info: Edge.Services.EventHubRelayHostService[0]\n'
                    '[{"DeviceId":"ESS-UNIT-81","Timestamp":"2026-04-16T17:39:10.3038222Z","Name":"Pump Rate BPM"}]\n'
                    "1776361151: Sending PUBLISH to relay-mqtt-client2 (d0, q0, r0, m0, 'outgoing', ... (264 bytes))\n"
                ),
                stderr="",
            ),
        ("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "opsviewer2-edge"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="172.17.0.3\n",
                stderr="",
            ),
        ("docker", "port", "opsviewer2-edge"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="1883/tcp -> 0.0.0.0:1883\n8080/tcp -> 0.0.0.0:8080\n9001/tcp -> 0.0.0.0:9001\n",
                stderr="",
            ),
        ("docker", "exec", "opsviewer2-edge", "mosquitto_sub", "-h", "127.0.0.1", "-p", "1883", "-t", "$SYS/broker/clients/connected", "-C", "1", "-W", "3"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="7\n",
                stderr="",
            ),
        ("docker", "logs", "--tail", "50", "plcreader"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "Sent: Packet size: 1 - Measurements: 43 - Queue Size 0 04/16/2026 17:36:02\n"
                    "Sending. 1-43 - 0 04/16/2026 17:36:03\n"
                ),
                stderr="",
            ),
    }

    def fake_run(config, args, check=True):
        key = tuple(arg for arg in args if arg not in {"sudo", "-n"})
        return outputs.get(key, subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="missing"))

    def fake_fetch(url, timeout=5):
        if "172.17.0.3" in url and "/api/Queue" in url:
            return queue_json, ""
        return "", f"failed: {url}"

    monkeypatch.setattr(datalogger_manager, "_run_docker_command", fake_run)
    monkeypatch.setattr(datalogger_manager, "_fetch_url", fake_fetch)

    status = datalogger_manager.get_datalogger_status(
        {
            "DOCKER_BIN": "docker",
            "PORTAINER_CONTAINER_NAME": "portainer",
            "MQTT_LOGGER_CONTAINER_NAME": "opsviewer2-edge",
            "PLC_LOGGER_CONTAINER_NAME": "plcreader",
        },
        host="ess-pi",
    )

    assert status["active_logger"] == "MQTT Logger"
    assert status["mqtt_logger"]["running"] is True
    assert status["mqtt_logger"]["device_id"] == "ESS-UNIT-81"
    assert status["mqtt_logger"]["summary"] == "Buffering 7 records locally"
    assert status["mqtt_logger"]["mqtt_ui_url"] == "http://ess-pi:8080"
    assert status["mqtt_logger"]["queue_size"] == 7
    assert status["mqtt_logger"]["broker_clients_connected"] == 7
    assert "172.17.0.3" in status["mqtt_logger"]["queue_source_url"]
    assert "push" in status["mqtt_logger"]["last_push_label"].lower()
    assert status["plc_logger"]["running"] is False
    assert status["plc_logger"]["measurements"] == 43
    assert status["plc_logger"]["queue_size"] == 0
    assert status["plc_logger"]["summary"] == "Last send OK"
    assert "PLC logger stopped" in status["warnings"]


def test_command_error_prefers_real_docker_message():
    result = subprocess.CompletedProcess(
        args=["docker", "run"],
        returncode=125,
        stdout="",
        stderr="docker: Error response from daemon: port is already allocated\n\nRun 'docker run --help' for more information\n",
    )

    message = datalogger_manager._command_error(result, "Unable to install and start Portainer")

    assert "port is already allocated" in message
    assert "Run 'docker run --help'" not in message


def test_build_portainer_url_prefers_https():
    url = datalogger_manager._build_portainer_url(
        {"PORTAINER_HOSTNAME": "ess-pi", "PORTAINER_HTTP_PORT": 9000, "PORTAINER_HTTPS_PORT": 9443}
    )

    assert url == "https://ess-pi:9443"


def test_parse_plc_logs_extracts_measurements_queue_and_timestamp():
    parsed = datalogger_manager._parse_plc_logger_logs(
        "Sent: Packet size: 1 - Measurements: 43 - Queue Size 0 04/16/2026 17:36:02\n"
        "Sending. 1-43 - 0 04/16/2026 17:36:03\n"
    )

    assert parsed["measurements"] == 43
    assert parsed["queue_size"] == 0
    assert parsed["summary"] == "Last send OK"
    assert parsed["last_activity_text"] == "04/16/2026 17:36:03"


def test_parse_mqtt_logs_extracts_device_and_publish_state():
    parsed = datalogger_manager._parse_mqtt_logger_logs(
        'info: Edge.Services.EventHubRelayHostService[0]\n'
        '[{"DeviceId":"ESS-UNIT-81","Timestamp":"2026-04-16T17:39:10.3038222Z","Name":"Pump Rate BPM"}]\n'
        "1776361151: Sending PUBLISH to relay-mqtt-client2 (d0, q0, r0, m0, 'outgoing', ... (264 bytes))\n"
    )

    assert parsed["device_id"] == "ESS-UNIT-81"
    assert parsed["channel_count"] == 1
    assert parsed["summary"] == "Data pushed successfully"
    assert parsed["last_activity_text"] == "2026-04-16T17:39:10.3038222Z"


def test_parse_mqtt_logs_extracts_queue_metrics_from_edge_ui_html():
    parsed = datalogger_manager._parse_mqtt_logger_logs(
        "<section><div>LENGTH</div><div>1089</div></section>"
    )

    assert parsed["queue_size"] == 1089


def test_read_mqtt_queue_metrics_uses_container_bridge_ip(monkeypatch):
    """Primary path: docker inspect gives the container IP, fetch /api/Queue."""
    queue_json = '{"Length": 7}'

    def fake_run(config, args, check=True):
        key = tuple(arg for arg in args if arg not in {"sudo", "-n"})
        if key == ("docker", "inspect", "-f",
                   "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                   "opsviewer2-edge"):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="172.17.0.3\n", stderr="")
        if key == ("docker", "port", "opsviewer2-edge"):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="8080/tcp -> 0.0.0.0:8080\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    def fake_fetch(url, timeout=5):
        if "172.17.0.3" in url and "/api/Queue" in url:
            return queue_json, ""
        return "", f"failed: {url}"

    monkeypatch.setattr(datalogger_manager, "_run_docker_command", fake_run)
    monkeypatch.setattr(datalogger_manager, "_fetch_url", fake_fetch)

    parsed = datalogger_manager._read_mqtt_queue_metrics(
        {}, "http://ess-pi:8080",
        docker_bin="docker", container_name="opsviewer2-edge",
    )

    assert parsed["queue_size"] == 7
    assert "172.17.0.3" in parsed["queue_source_url"]
    assert "/api/Queue" in parsed["queue_source_url"]


def test_read_mqtt_queue_metrics_falls_back_to_public_url(monkeypatch):
    """When docker inspect fails, fall back to the public mqtt_ui_url."""
    queue_json = '{"Length": 11}'

    def fake_run(config, args, check=True):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    def fake_fetch(url, timeout=5):
        if "ess-pi" in url and "/api/Queue" in url:
            return queue_json, ""
        return "", f"failed: {url}"

    monkeypatch.setattr(datalogger_manager, "_run_docker_command", fake_run)
    monkeypatch.setattr(datalogger_manager, "_fetch_url", fake_fetch)

    parsed = datalogger_manager._read_mqtt_queue_metrics(
        {}, "http://ess-pi:8080",
        docker_bin="docker", container_name="opsviewer2-edge",
    )

    assert parsed["queue_size"] == 11
    assert "ess-pi" in parsed["queue_source_url"]
    assert "/api/Queue" in parsed["queue_source_url"]
