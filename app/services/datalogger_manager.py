from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


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
            detected_mqtt_ui_url = _discover_mqtt_ui_url(config, docker_bin, mqtt_container_name, host=host)
            if detected_mqtt_ui_url:
                status["mqtt_ui_url"] = detected_mqtt_ui_url
            status["mqtt_logger"]["mqtt_ui_url"] = status["mqtt_ui_url"]
            log_output = _read_container_logs(config, docker_bin, mqtt_container_name)
            status["mqtt_logger"].update(_parse_mqtt_logger_logs(log_output))
            status["mqtt_logger"].update(
                _read_mqtt_queue_metrics(
                    config,
                    status["mqtt_ui_url"],
                    docker_bin=docker_bin,
                    container_name=mqtt_container_name,
                )
            )
            status["mqtt_logger"] = _finalize_logger_state(status["mqtt_logger"])

        if name == plc_container_name:
            status["plc_logger"].update(container)
            status["plc_logger"]["running"] = container_status.lower().startswith("up")
            log_output = _read_container_logs(config, docker_bin, plc_container_name)
            status["plc_logger"].update(_parse_plc_logger_logs(log_output))
            status["plc_logger"] = _finalize_logger_state(status["plc_logger"])

    status["containers"] = containers
    status["mqtt_logger"] = _finalize_logger_state(status["mqtt_logger"])
    status["plc_logger"] = _finalize_logger_state(status["plc_logger"])
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
    port = str(config.get("MQTT_UI_PORT", "")).strip()
    if not port:
        return ""
    return f"http://{hostname}:{port}"


def _discover_mqtt_ui_url(config: dict[str, Any], docker_bin: str, container_name: str, host: str | None = None) -> str:
    configured_url = _build_mqtt_ui_url(config, host=host)
    if configured_url:
        return configured_url

    result = _run_docker_command(config, [docker_bin, "port", container_name], check=False)
    if result.returncode == 0:
        host_port = _extract_mqtt_host_port(result.stdout)
        if host_port:
            hostname = host or config.get("MQTT_UI_HOSTNAME") or config.get("PORTAINER_HOSTNAME") or "localhost"
            return f"http://{hostname}:{host_port}"

    return ""


def _extract_mqtt_host_port(port_output: str) -> str:
    for line in port_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not any(token in stripped.lower() for token in ("80/tcp", "8080/tcp", "5000/tcp", "3000/tcp")):
            continue
        match = re.search(r":(\d+)\s*$", stripped)
        if match:
            return match.group(1)

    fallback = re.search(r":(\d+)\s*$", port_output.strip())
    return fallback.group(1) if fallback else ""


def _default_logger_state(label: str, container_name: str) -> dict[str, Any]:
    return {
        "label": label,
        "name": container_name,
        "image": "",
        "status": "Not found",
        "running": False,
        "summary": "No recent activity",
        "last_activity_text": "Unknown",
        "last_push_age_seconds": None,
        "last_push_label": "Waiting for data",
        "status_class": "status-neutral",
        "error": "",
        "measurements": None,
        "queue_size": None,
        "success_rate": None,
        "failure_rate": None,
        "success_samples": None,
        "failure_samples": None,
        "queue_source_url": "",
        "queue_fetch_error": "",
        "device_id": "",
        "channel_count": None,
    }


def _read_container_logs(config: dict[str, Any], docker_bin: str, container_name: str, tail_lines: int = 50) -> str:
    result = _run_docker_command(config, [docker_bin, "logs", "--tail", str(tail_lines), container_name], check=False)
    return result.stdout if result.returncode == 0 else ""


def _parse_plc_logger_logs(log_text: str) -> dict[str, Any]:
    parsed = {
        "summary": "No recent activity",
        "last_activity_text": "Unknown",
        "measurements": None,
        "queue_size": None,
        "error": "",
    }
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
    parsed = {
        "summary": "No recent activity",
        "last_activity_text": "Unknown",
        "device_id": "",
        "channel_count": None,
        "queue_size": None,
        "success_rate": None,
        "failure_rate": None,
        "success_samples": None,
        "failure_samples": None,
        "queue_source_url": "",
        "queue_fetch_error": "",
        "error": "",
    }
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

    parsed.update(_extract_mqtt_queue_metrics(log_text))

    if "Sending PUBLISH" in log_text or "Received PUBLISH" in log_text:
        parsed["summary"] = "Data pushed successfully"
    if re.search(r"error|exception|failed", log_text, re.IGNORECASE):
        parsed["summary"] = "Error detected"
        parsed["error"] = _last_meaningful_line(log_text)

    return parsed


def _finalize_logger_state(logger: dict[str, Any]) -> dict[str, Any]:
    logger = dict(logger)
    logger.setdefault("last_push_age_seconds", None)
    logger.setdefault("last_push_label", "Waiting for data")
    logger.setdefault("status_class", "status-neutral")

    if logger.get("error"):
        logger["status_class"] = "status-offline"
        logger["last_push_label"] = "Push error detected"
        return logger

    if not logger.get("running"):
        logger["status_class"] = "status-offline"
        logger["last_push_label"] = "Logger stopped"
        return logger

    queue_size = logger.get("queue_size")
    if isinstance(queue_size, int) and queue_size > 0 and logger.get("label") == "MQTT Logger":
        logger["summary"] = f"Buffering {queue_size} records locally"
        logger["status_class"] = "status-warning"

    timestamp = _parse_activity_timestamp(logger.get("last_activity_text", ""))
    if timestamp is None:
        logger["status_class"] = "status-neutral"
        logger["last_push_label"] = "Waiting for data"
        return logger

    if timestamp.tzinfo is not None:
        age_seconds = max(0, int((datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()))
    else:
        age_seconds = max(0, int((datetime.now() - timestamp).total_seconds()))

    logger["last_push_age_seconds"] = age_seconds
    if age_seconds <= 15:
        if logger.get("status_class") != "status-warning":
            logger["status_class"] = "status-online"
        logger["last_push_label"] = _format_last_push_label(age_seconds)
    elif age_seconds <= 60:
        if logger.get("status_class") != "status-warning":
            logger["status_class"] = "status-neutral"
        logger["last_push_label"] = _format_last_push_label(age_seconds)
    else:
        logger["status_class"] = "status-offline"
        logger["last_push_label"] = f"No push seen for {max(1, age_seconds // 60)} min"

    return logger


def _parse_activity_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text or text == "Unknown":
        return None

    try:
        if "T" in text:
            normalized = text.replace("Z", "+00:00")
            match = re.match(r"^(.*?\.)(\d+)([+-]\d\d:\d\d)$", normalized)
            if match:
                head, fraction, suffix = match.groups()
                normalized = f"{head}{fraction[:6]}{suffix}"
            return datetime.fromisoformat(normalized)
        return datetime.strptime(text, "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def _format_last_push_label(age_seconds: int) -> str:
    if age_seconds < 60:
        return f"Last pushed {age_seconds} sec ago"
    if age_seconds < 3600:
        return f"Last pushed {max(1, age_seconds // 60)} min ago"
    return f"Last pushed {max(1, age_seconds // 3600)} hr ago"


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
    queue_size = mqtt_logger.get("queue_size")
    if isinstance(queue_size, int) and queue_size > 0:
        warnings.append(f"MQTT queue building: {queue_size} buffered")
    return warnings


def _read_mqtt_queue_metrics(
    config: dict[str, Any],
    mqtt_ui_url: str,
    docker_bin: str | None = None,
    container_name: str | None = None,
) -> dict[str, Any]:
    last_error = ""
    last_url = ""
    debug_notes: list[str] = []

    timeout = max(1.0, min(5.0, float(config.get("VERIFY_POLL_SECONDS", 2))))
    candidate_paths = ["/tools/queue", "/tools/queue/", "/QueueStatus", "/"]

    # Collect all (host, port) pairs to try.
    targets: list[tuple[str, str]] = []

    # From docker inspect + docker port: get the container's bridge IP and
    # the actual port mappings (container_port -> host_port).
    container_ip = ""
    if docker_bin and container_name:
        container_ip = _get_container_ip(config, docker_bin, container_name)
        port_mappings = _get_container_port_mappings(config, docker_bin, container_name)
        if container_ip:
            debug_notes.append(f"container_ip={container_ip}")
            # Try bridge IP on each container port (e.g. 8080, 9001).
            for container_port, host_port in port_mappings:
                targets.append((container_ip, container_port))
            if not port_mappings:
                # Fallback: common web ports.
                for p in ("8080", "80", "443"):
                    targets.append((container_ip, p))
        else:
            debug_notes.append("container_ip=none")

        # Also try loopback on each host-mapped port.
        if port_mappings:
            debug_notes.append(f"ports={','.join(f'{c}->{h}' for c, h in port_mappings)}")
            for container_port, host_port in port_mappings:
                targets.append(("127.0.0.1", host_port))

    # The mqtt_ui_url (e.g. http://192.168.0.11:8080).
    original_host = ""
    if mqtt_ui_url:
        parsed_base = urlparse(mqtt_ui_url)
        original_host = parsed_base.hostname or ""
        port = str(parsed_base.port or 8080)
        debug_notes.append(f"mqtt_url_host={original_host}:{port}")
        targets.append((original_host, port))
        # Also try loopback on the same port.
        targets.append(("127.0.0.1", port))

    # Deduplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    unique_targets: list[tuple[str, str]] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            unique_targets.append(t)

    # For each target, try HTTP and HTTPS, with and without Host header.
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    for host, port in unique_targets:
        for scheme in ("http", "https"):
            for path in candidate_paths:
                url = f"{scheme}://{host}:{port}{path}"
                last_url = url
                for use_host_header in (False, True):
                    if use_host_header and not original_host:
                        continue
                    if use_host_header and host == original_host:
                        continue
                    try:
                        req_headers = {
                            "User-Agent": "ESS-Datalogger-UI/1.0",
                            "Accept": "text/html,application/json",
                        }
                        if use_host_header:
                            req_headers["Host"] = original_host
                        request = Request(url, headers=req_headers)
                        ctx = ssl_ctx if scheme == "https" else None
                        with urlopen(request, timeout=timeout, context=ctx) as response:
                            payload = response.read().decode("utf-8", errors="ignore")
                    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                        last_error = str(exc)
                        continue

                    parsed = _extract_mqtt_queue_metrics(payload)
                    if any(parsed.get(key) is not None for key in ("queue_size", "success_rate", "failure_rate")):
                        parsed["queue_source_url"] = url
                        parsed["queue_fetch_error"] = ""
                        return parsed

    diag = "; ".join(debug_notes) if debug_notes else ""
    full_error = f"{last_error} [{diag}]" if diag else last_error
    return {
        "queue_size": None,
        "success_rate": None,
        "failure_rate": None,
        "success_samples": None,
        "failure_samples": None,
        "queue_source_url": last_url,
        "queue_fetch_error": full_error,
    }


def _get_container_ip(config: dict[str, Any], docker_bin: str, container_name: str) -> str:
    """Return the Docker-internal bridge IP of a running container, or ''."""
    result = _run_docker_command(
        config,
        [docker_bin, "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
         container_name],
        check=False,
    )
    if result.returncode == 0:
        ip = result.stdout.strip()
        if ip and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            return ip
    return ""


def _get_container_port_mappings(config: dict[str, Any], docker_bin: str, container_name: str) -> list[tuple[str, str]]:
    """Return list of (container_port, host_port) tuples from ``docker port``."""
    result = _run_docker_command(
        config, [docker_bin, "port", container_name], check=False,
    )
    mappings: list[tuple[str, str]] = []
    if result.returncode != 0:
        return mappings
    for line in result.stdout.splitlines():
        # e.g. "8080/tcp -> 0.0.0.0:8080"
        m = re.match(r"(\d+)/\w+\s+->\s+[\d.]+:(\d+)", line.strip())
        if m:
            mappings.append((m.group(1), m.group(2)))
    return mappings


def _extract_mqtt_queue_metrics(text: str) -> dict[str, Any]:
    parsed = {
        "queue_size": None,
        "success_rate": None,
        "failure_rate": None,
        "success_samples": None,
        "failure_samples": None,
    }
    if not text:
        return parsed

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        lower_payload = {str(key).lower(): value for key, value in payload.items()}
        parsed["queue_size"] = _safe_int(lower_payload.get("length", lower_payload.get("queue_size")))
        parsed["success_rate"] = _safe_float(lower_payload.get("success rate (sec)", lower_payload.get("success_rate")))
        parsed["failure_rate"] = _safe_float(lower_payload.get("failure rate (sec)", lower_payload.get("failure_rate")))
        parsed["success_samples"] = _safe_int(lower_payload.get("success samples", lower_payload.get("success_samples")))
        parsed["failure_samples"] = _safe_int(lower_payload.get("failure samples", lower_payload.get("failure_samples")))
        return parsed

    normalized = unescape(re.sub(r"<[^>]+>", " ", text))
    normalized = re.sub(r"\s+", " ", normalized).strip()

    queue_match = re.search(r"LENGTH\s+([0-9]+)", normalized, re.IGNORECASE)
    success_rate_match = re.search(r"SUCCESS RATE \(SEC\)\s+([0-9]*\.?[0-9]+)", normalized, re.IGNORECASE)
    success_samples_match = re.search(r"SUCCESS SAMPLES\s+([0-9]+)", normalized, re.IGNORECASE)
    failure_rate_match = re.search(r"FAILURE RATE \(SEC\)\s+([0-9]*\.?[0-9]+)", normalized, re.IGNORECASE)
    failure_samples_match = re.search(r"FAILURE SAMPLES\s+([0-9]+)", normalized, re.IGNORECASE)

    if queue_match:
        parsed["queue_size"] = int(queue_match.group(1))
    if success_rate_match:
        parsed["success_rate"] = float(success_rate_match.group(1))
    if success_samples_match:
        parsed["success_samples"] = int(success_samples_match.group(1))
    if failure_rate_match:
        parsed["failure_rate"] = float(failure_rate_match.group(1))
    if failure_samples_match:
        parsed["failure_samples"] = int(failure_samples_match.group(1))

    return parsed


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
