import subprocess

from app.services import datalogger_manager


def test_get_datalogger_status_parses_logger_roles_and_health(monkeypatch):
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
        ("docker", "port", "opsviewer2-edge"):
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="80/tcp -> 0.0.0.0:5055\n",
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

    monkeypatch.setattr(datalogger_manager, "_run_docker_command", fake_run)

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
    assert status["mqtt_logger"]["summary"] == "Data pushed successfully"
    assert status["mqtt_logger"]["mqtt_ui_url"] == "http://ess-pi:5055"
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
        "<section><div>LENGTH</div><div>1089</div><div>SUCCESS RATE (SEC)</div><div>0.18</div>"
        "<div>SUCCESS SAMPLES</div><div>275</div><div>FAILURE RATE (SEC)</div><div>1.31</div>"
        "<div>FAILURE SAMPLES</div><div>300</div></section>"
    )

    assert parsed["queue_size"] == 1089
    assert parsed["success_rate"] == 0.18
    assert parsed["failure_rate"] == 1.31
    assert parsed["success_samples"] == 275
    assert parsed["failure_samples"] == 300


def test_read_mqtt_queue_metrics_follows_queue_status_link_from_root_page(monkeypatch):
    responses = {
        "http://ess-pi:8080/": '<html><body><a href="/QueueStatus">Queue Status</a></body></html>',
        "http://ess-pi:8080/QueueStatus": (
            "<section><div>LENGTH</div><div>1089</div><div>SUCCESS RATE (SEC)</div><div>0.18</div>"
            "<div>SUCCESS SAMPLES</div><div>275</div><div>FAILURE RATE (SEC)</div><div>1.31</div>"
            "<div>FAILURE SAMPLES</div><div>300</div></section>"
        ),
    }

    class FakeResponse:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url in responses:
            return FakeResponse(responses[url])
        raise datalogger_manager.URLError(f"unexpected URL: {url}")

    monkeypatch.setattr(datalogger_manager, "urlopen", fake_urlopen)

    parsed = datalogger_manager._read_mqtt_queue_metrics({}, "http://ess-pi:8080/")

    assert parsed["queue_size"] == 1089
    assert parsed["success_rate"] == 0.18
    assert parsed["failure_rate"] == 1.31
    assert parsed["queue_source_url"] == "http://ess-pi:8080/QueueStatus"


def test_read_mqtt_queue_metrics_supports_tools_queue_route(monkeypatch):
    responses = {
        "http://ess-pi:8080/tools/queue": (
            "<section><div>LENGTH</div><div>7</div><div>SUCCESS RATE (SEC)</div><div>0.92</div>"
            "<div>FAILURE RATE (SEC)</div><div>0.03</div><div>FAILURE SAMPLES</div><div>101</div></section>"
        )
    }

    class FakeResponse:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url in responses:
            return FakeResponse(responses[url])
        raise datalogger_manager.URLError(f"unexpected URL: {url}")

    monkeypatch.setattr(datalogger_manager, "urlopen", fake_urlopen)

    parsed = datalogger_manager._read_mqtt_queue_metrics({}, "http://ess-pi:8080")

    assert parsed["queue_size"] == 7
    assert parsed["success_rate"] == 0.92
    assert parsed["failure_rate"] == 0.03
    assert parsed["queue_source_url"] == "http://ess-pi:8080/tools/queue"
