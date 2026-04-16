from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any


class DataloggerManagerError(RuntimeError):
    pass


def get_datalogger_status(config: dict[str, Any], host: str | None = None) -> dict[str, Any]:
    docker_bin = config.get("DOCKER_BIN", "docker")
    container_name = config.get("PORTAINER_CONTAINER_NAME", "portainer")
    mqtt_container_name = config.get("MQTT_LOGGER_CONTAINER_NAME", "opsviewer2-edge")
    plc_container_name = config.get("PLC_LOGGER_CONTAINER_NAME", "plcreader")

    status: dict[str, Any] = {
        "docker_available": False,
        "docker_running": False,
        "portainer_installed": False,
        "portainer_running": False,
        "portainer_url": _build_portainer_url(config, host=host),
        "mqtt_ui_url": _build_mqtt_ui_url(config, host=host),
        "containers": [],
        "active_logger": "No Logger Running",
        "warnings": [],
        "mqtt_logger": _default_logger_state("MQTT Logger", mqtt_container_name),
        "plc_logger": _default_logger_state("PLC Logger", plc_container_name),
        "error": "",
    }

    version_result = _run_docker_command(config, [docker_bin, "version", "--format", "{{.Server.Version}}"], check=False)
    if version_result.returncode != 0:
        status["error"] = _command_error(version_result, "Docker is not available or the daemon is not running")
        return status

    status["docker_available"] = True
    status["docker_running"] = True

    ps_result = _run_docker_command(
        config,
        [docker_bin, "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"],
        check=False,
    )
    if ps_result.returncode != 0:
        status["error"] = _command_error(ps_result, "Unable to read Docker container status")
        return status

    containers = []
    for line in ps_result.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        name, image, container_status = [part.strip() for part in parts]
        container = {"name": name, "image": image, "status": container_status}
        containers.append(container)

        if name == container_name or "portainer" in image.lower():
            status["portainer_installed"] = True
            if container_status.lower().startswith("up"):
                status["portainer_running"] = True

        if name == mqtt_container_name:
            status["mqtt_logger"].update(container)
            status["mqtt_logger"]["running"] = container_status.lower().startswith("up")
            status["mqtt_logger"]["mqtt_ui_url"] = status["mqtt_ui_url"]
            log_output = _read_container_logs(config, docker_bin, mqtt_container_name)
            status["mqtt_logger"].update(_parse_mqtt_logger_logs(log_output))

        if name == plc_container_name:
            status["plc_logger"].update(container)
            status["plc_logger"]["running"] = container_status.lower().startswith("up")
            log_output = _read_container_logs(config, docker_bin, plc_container_name)
            status["plc_logger"].update(_parse_plc_logger_logs(log_output))

    status["containers"] = containers
    status["active_logger"] = _determine_active_logger(status["mqtt_logger"], status["plc_logger"])
    status["warnings"] = _build_logger_warnings(status["mqtt_logger"], status["plc_logger"])
    return status


def ensure_portainer(config: dict[str, Any]) -> dict[str, Any]:
    if not _is_linux_target():
        return {"success": False, "message": "Portainer control is only available on the Pi target device."}

    status = get_datalogger_status(config)
    if not status["docker_available"]:
        return {"success": False, "message": "Docker is not available yet. Install Docker on the Pi first."}

    docker_bin = config.get("DOCKER_BIN", "docker")
    container_name = config.get("PORTAINER_CONTAINER_NAME", "portainer")
    http_port = str(config.get("PORTAINER_HTTP_PORT", 9000)).strip()
    https_port = str(config.get("PORTAINER_HTTPS_PORT", 9443)).strip()
    edge_port = str(config.get("PORTAINER_EDGE_PORT", 8000)).strip()

    if status["portainer_running"]:
        return {"success": True, "message": "Portainer is already running and ready to use."}

    if status["portainer_installed"]:
        result = _run_docker_command(config, [docker_bin, "start", container_name], check=False)
        if result.returncode != 0:
            return {"success": False, "message": _command_error(result, "Unable to start the existing Portainer container")}
        return {"success": True, "message": "Portainer started successfully."}

    volume_result = _run_docker_command(config, [docker_bin, "volume", "create", "portainer_data"], check=False)
    if volume_result.returncode != 0:
        return {"success": False, "message": _command_error(volume_result, "Unable to create the Portainer data volume")}

    command = [
        docker_bin,
        "run",
        "-d",
        "--name",
        container_name,
        "--restart=always",
        "-p",
        f"{edge_port}:8000",
        "-p",
        f"{https_port}:9443",
    ]
    if http_port and http_port != "0":
        command.extend(["-p", f"{http_port}:9000"])
    command.extend(
        [
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            "portainer_data:/data",
            "portainer/portainer-ce:sts",
        ]
    )

    run_result = _run_docker_command(config, command, check=False)
    if run_result.returncode != 0:
        return {"success": False, "message": _command_error(run_result, "Unable to install and start Portainer")}

    return {"success": True, "message": "Portainer installed and started successfully."}


def _build_portainer_url(config: dict[str, Any], host: str | None = None) -> str:
    hostname = host or config.get("PORTAINER_HOSTNAME") or "localhost"
    https_port = str(config.get("PORTAINER_HTTPS_PORT", 9443)).strip()
    if https_port and https_port != "0":
        return f"https://{hostname}:{https_port}"
    return f"http://{hostname}:{config.get('PORTAINER_HTTP_PORT', 9000)}"


def _build_mqtt_ui_url(config: dict[str, Any], host: str | None = None) -> str:
    hostname = host or config.get("MQTT_UI_HOSTNAME") or config.get("PORTAINER_HOSTNAME") or "localhost"
    port = str(config.get("MQTT_UI_PORT", 8080)).strip()
    return f"http://{hostname}:{port}"


def _default_logger_state(label: str, container_name: str) -> dict[str, Any]:
    return {
        "label": label,
        "name": container_name,
        "image": "",
        "status": "Not found",
        "running": False,
        "summary": "No recent activity",
        "last_activity_text": "Unknown",
        "error": "",
        "measurements": None,
        "queue_size": None,
        "device_id": "",
        "channel_count": None,
    }


def _read_container_logs(config: dict[str, Any], docker_bin: str, container_name: str, tail_lines: int = 50) -> str:
    result = _run_docker_command(config, [docker_bin, "logs", "--tail", str(tail_lines), container_name], check=False)
    return result.stdout if result.returncode == 0 else ""


def _parse_plc_logger_logs(log_text: str) -> dict[str, Any]:
    parsed = {"summary": "No recent activity", "last_activity_text": "Unknown", "measurements": None, "queue_size": None, "error": ""}
    if not log_text:
        return parsed

    measurements_match = re.search(r"Measurements:\s*(\d+)", log_text)
    queue_match = re.search(r"Queue Size\s*(\d+)", log_text)
    timestamps = re.findall(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", log_text)

    if measurements_match:
        parsed["measurements"] = int(measurements_match.group(1))
    if queue_match:
        parsed["queue_size"] = int(queue_match.group(1))
    if timestamps:
        parsed["last_activity_text"] = timestamps[-1]
    if "Sending." in log_text or "Sent:" in log_text:
        parsed["summary"] = "Last send OK"
    if re.search(r"error|exception|failed", log_text, re.IGNORECASE):
        parsed["summary"] = "Error detected"
        parsed["error"] = _last_meaningful_line(log_text)

    return parsed


def _parse_mqtt_logger_logs(log_text: str) -> dict[str, Any]:
    parsed = {"summary": "No recent activity", "last_activity_text": "Unknown", "device_id": "", "channel_count": None, "error": ""}
    if not log_text:
        return parsed

    for line in log_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and '"DeviceId"' in stripped:
            try:
                payload = json.loads(stripped)
                if isinstance(payload, list) and payload:
                    parsed["device_id"] = str(payload[0].get("DeviceId", ""))
                    parsed["last_activity_text"] = str(payload[0].get("Timestamp", "Unknown"))
                    parsed["channel_count"] = len(payload)
            except json.JSONDecodeError:
                pass

    if "Sending PUBLISH" in log_text or "Received PUBLISH" in log_text:
        parsed["summary"] = "Relay traffic OK"
    if re.search(r"error|exception|failed", log_text, re.IGNORECASE):
        parsed["summary"] = "Error detected"
        parsed["error"] = _last_meaningful_line(log_text)

    return parsed


def _determine_active_logger(mqtt_logger: dict[str, Any], plc_logger: dict[str, Any]) -> str:
    if mqtt_logger.get("running") and plc_logger.get("running"):
        return "Both Running"
    if mqtt_logger.get("running"):
        return "MQTT Logger"
    if plc_logger.get("running"):
        return "PLC Logger"
    return "No Logger Running"


def _build_logger_warnings(mqtt_logger: dict[str, Any], plc_logger: dict[str, Any]) -> list[str]:
    warnings = []
    if mqtt_logger.get("running") and plc_logger.get("running"):
        warnings.append("Both loggers are running")
    if not plc_logger.get("running"):
        warnings.append("PLC logger stopped")
    if mqtt_logger.get("summary") == "Error detected":
        warnings.append("MQTT logger error detected")
    if plc_logger.get("summary") == "Error detected":
        warnings.append("PLC logger error detected")
    return warnings


def _last_meaningful_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _run_docker_command(
    config: dict[str, Any],
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = list(args)
    if config.get("USE_SUDO_FOR_DOCKER", True) and command[0] != config.get("SUDO_BIN", "sudo"):
        command.insert(0, config.get("SUDO_BIN", "sudo"))
        command.insert(1, "-n")

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise DataloggerManagerError(_command_error(result, "Docker command failed"))
    return result


def _command_error(result: subprocess.CompletedProcess[str], prefix: str) -> str:
    detail_lines = [line.strip() for line in (result.stderr or result.stdout or "unknown error").splitlines() if line.strip()]
    if not detail_lines:
        return prefix

    for line in reversed(detail_lines):
        if "--help" in line.lower() or line.lower().startswith("see '"):
            continue
        return f"{prefix}: {line}"

    return f"{prefix}: {detail_lines[-1]}"


def _is_linux_target() -> bool:
    return os.name == "posix" and os.uname().sysname.lower() == "linux"
