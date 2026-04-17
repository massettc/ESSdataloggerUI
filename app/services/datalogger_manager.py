from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlparse


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
        "system_status_label": "Checking status",
        "system_status_class": "status-neutral",
        "system_status_detail": "Waiting for live logger data.",
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
            status["mqtt_logger"]["broker_clients_connected"] = _read_broker_clients(
                config, docker_bin, mqtt_container_name,
            )
            status["mqtt_logger"] = _finalize_logger_state(status["mqtt_logger"])

        if name == plc_container_name:
            status["plc_logger"].update(container)
            status["plc_logger"]["running"] = container_status.lower().startswith("up")
            log_output = _read_container_logs(config, docker_bin, plc_container_name)
            status["plc_logger"].update(_parse_plc_logger_logs(log_output))
            status["plc_logger"] = _finalize_logger_state(status["plc_logger"])

    status["containers"] = containers
    status["mqtt_logger"] = _decorate_mqtt_logger_state(_finalize_logger_state(status["mqtt_logger"]))
    status["plc_logger"] = _decorate_plc_logger_state(_finalize_logger_state(status["plc_logger"]))
    status["active_logger"] = _determine_active_logger(status["mqtt_logger"], status["plc_logger"])
    status["warnings"] = _build_logger_warnings(status["mqtt_logger"], status["plc_logger"])
    status.update(_build_system_status(status["mqtt_logger"], status["plc_logger"], status["warnings"]))
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
        "card_title": "Cloud delivery" if label == "MQTT Logger" else "PLC connection",
        "connection_label": "Waiting",
        "connection_class": "status-neutral",
        "error": "",
        "measurements": None,
        "queue_size": None,
        "queue_source_url": "",
        "queue_fetch_error": "",
        "broker_clients_connected": None,
        "device_id": "",
        "channel_count": None,
    }


def _read_container_logs(config: dict[str, Any], docker_bin: str, container_name: str, tail_lines: int = 50) -> str:
    result = _run_docker_command(config, [docker_bin, "logs", "--tail", str(tail_lines), container_name], check=False)
    return result.stdout if result.returncode == 0 else ""


def _read_broker_clients(config: dict[str, Any], docker_bin: str, container_name: str) -> int | None:
    """Query mosquitto $SYS topic for connected client count via docker exec."""
    result = _run_docker_command(
        config,
        [
            docker_bin, "exec", container_name,
            "mosquitto_sub", "-h", "127.0.0.1", "-p", "1883",
            "-t", "$SYS/broker/clients/connected",
            "-C", "1", "-W", "3",
        ],
        check=False,
    )
    if result.returncode == 0:
        text = result.stdout.strip()
        try:
            return int(text)
        except (ValueError, TypeError):
            pass
    return None


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
        "queue_source_url": "",
        "queue_fetch_error": "",
        "broker_clients_connected": None,
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


def _decorate_mqtt_logger_state(logger: dict[str, Any]) -> dict[str, Any]:
    logger = dict(logger)
    logger["card_title"] = "Cloud delivery"

    if logger.get("error"):
        logger["connection_label"] = "Send error"
        logger["connection_class"] = "status-offline"
        return logger

    if not logger.get("running"):
        logger["connection_label"] = "Stopped"
        logger["connection_class"] = "status-offline"
        logger["summary"] = "Cloud delivery is not running"
        return logger

    queue_size = logger.get("queue_size")
    age_seconds = logger.get("last_push_age_seconds")

    if isinstance(queue_size, int) and queue_size > 0:
        logger["connection_label"] = "Backlog detected"
        logger["connection_class"] = "status-warning"
        logger["summary"] = f"Buffering {queue_size} records locally"
    elif isinstance(age_seconds, int) and age_seconds <= 60:
        logger["connection_label"] = "Connected"
        logger["connection_class"] = "status-online"
        if logger.get("summary") in {"No recent activity", "Data pushed successfully"}:
            logger["summary"] = "Sending data to cloud"
    else:
        logger["connection_label"] = "Waiting"
        logger["connection_class"] = "status-neutral"

    return logger


def _decorate_plc_logger_state(logger: dict[str, Any]) -> dict[str, Any]:
    logger = dict(logger)
    logger["card_title"] = "PLC connection"

    if logger.get("error"):
        logger["connection_label"] = "PLC error"
        logger["connection_class"] = "status-offline"
        logger["summary"] = "PLC logging error detected"
        return logger

    if not logger.get("running"):
        logger["connection_label"] = "Not connected"
        logger["connection_class"] = "status-offline"
        logger["summary"] = "PLC logger is stopped"
        return logger

    age_seconds = logger.get("last_push_age_seconds")
    measurements = logger.get("measurements")

    if (isinstance(age_seconds, int) and age_seconds <= 60) or measurements is not None:
        logger["connection_label"] = "Connected"
        logger["connection_class"] = "status-online"
        if measurements is not None:
            logger["summary"] = f"Logging from PLC ({measurements} measurements)"
        else:
            logger["summary"] = "Logging from PLC"
    else:
        logger["connection_label"] = "Waiting"
        logger["connection_class"] = "status-neutral"
        logger["summary"] = "Waiting for PLC data"

    return logger


def _build_system_status(
    mqtt_logger: dict[str, Any],
    plc_logger: dict[str, Any],
    warnings: list[str],
) -> dict[str, str]:
    if mqtt_logger.get("error") or plc_logger.get("error"):
        detail = mqtt_logger.get("error") or plc_logger.get("error") or "A logger reported an error."
        return {
            "system_status_label": "Action needed",
            "system_status_class": "status-offline",
            "system_status_detail": detail,
        }

    queue_size = mqtt_logger.get("queue_size")
    if not plc_logger.get("running"):
        return {
            "system_status_label": "PLC disconnected",
            "system_status_class": "status-offline",
            "system_status_detail": "The PLC logger is not currently running.",
        }

    if not mqtt_logger.get("running"):
        return {
            "system_status_label": "Cloud send stopped",
            "system_status_class": "status-offline",
            "system_status_detail": "Cloud delivery is not currently running.",
        }

    if isinstance(queue_size, int) and queue_size > 0:
        return {
            "system_status_label": "Backlog building",
            "system_status_class": "status-warning",
            "system_status_detail": f"Cloud send is active, but {queue_size} records are buffered locally.",
        }

    if mqtt_logger.get("running") and plc_logger.get("running"):
        return {
            "system_status_label": "System healthy",
            "system_status_class": "status-online",
            "system_status_detail": "PLC logging and cloud delivery are both active.",
        }

    if warnings:
        return {
            "system_status_label": "Check system",
            "system_status_class": "status-neutral",
            "system_status_detail": warnings[0],
        }

    return {
        "system_status_label": "Waiting for data",
        "system_status_class": "status-neutral",
        "system_status_detail": "Waiting for the next logger update.",
    }


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
    if not plc_logger.get("running"):
        warnings.append("PLC logger stopped")
    if mqtt_logger.get("summary") == "Error detected" or mqtt_logger.get("error"):
        warnings.append("Cloud delivery error detected")
    if plc_logger.get("summary") == "Error detected" or plc_logger.get("error"):
        warnings.append("PLC logger error detected")
    queue_size = mqtt_logger.get("queue_size")
    if isinstance(queue_size, int) and queue_size > 0:
        warnings.append(f"Cloud backlog: {queue_size} buffered")
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

    candidate_paths = [
        "/api/Queue",
        "/api/queue",
    ]

    # Ports that are definitely NOT HTTP — skip them to save time.
    _NON_HTTP_PORTS = {"1883", "8883", "9001"}  # MQTT, MQTT-TLS, WebSocket

    # Collect all base URLs to try.
    base_urls: list[str] = []

    # From docker inspect + docker port: bridge IP on each container port.
    if docker_bin and container_name:
        container_ip = _get_container_ip(config, docker_bin, container_name)
        port_mappings = _get_container_port_mappings(config, docker_bin, container_name)
        if container_ip:
            debug_notes.append(f"container_ip={container_ip}")
            for container_port, host_port in port_mappings:
                if container_port not in _NON_HTTP_PORTS:
                    base_urls.append(f"http://{container_ip}:{container_port}")
        else:
            debug_notes.append("container_ip=none")

        if port_mappings:
            debug_notes.append(f"ports={','.join(f'{c}->{h}' for c, h in port_mappings)}")

    if mqtt_ui_url:
        parsed_base = urlparse(mqtt_ui_url)
        original_host = parsed_base.hostname or ""
        port = str(parsed_base.port or 8080)
        debug_notes.append(f"mqtt_url_host={original_host}:{port}")
        base_urls.append(f"http://{original_host}:{port}")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_bases: list[str] = []
    for b in base_urls:
        if b not in seen:
            seen.add(b)
            unique_bases.append(b)

    # Build full candidate URLs.
    candidate_urls: list[str] = []
    for base in unique_bases:
        for path in candidate_paths:
            candidate_urls.append(f"{base}{path}")

    # Use wget/curl from the host OS — Python's urllib gets connection
    # drops from the .NET Kestrel server inside the container.
    # Hard budget of 12 s so we never exceed gunicorn's 30 s worker timeout.
    deadline = time.monotonic() + 12
    attempt_errors: list[str] = []
    for url in candidate_urls:
        if time.monotonic() >= deadline:
            last_error = "time budget exceeded"
            break
        last_url = url
        remaining = max(1, int(deadline - time.monotonic()))
        per_url_timeout = min(2, remaining)
        payload, error = _fetch_url(url, timeout=per_url_timeout)
        if error:
            last_error = error
            attempt_errors.append(f"{url} => {error}")
            continue
        if not payload:
            attempt_errors.append(f"{url} => empty response")
            continue

        parsed = _extract_mqtt_queue_metrics(payload)
        if parsed.get("queue_size") is not None:
            parsed["queue_source_url"] = url
            parsed["queue_fetch_error"] = ""
            return parsed
        # Show a compact summary for parse failures.
        attempt_errors.append(f"{url} => no metrics ({len(payload)}B)")

    diag = "; ".join(debug_notes) if debug_notes else ""
    attempts = " | ".join(attempt_errors) if attempt_errors else "no attempts"
    full_error = f"{last_error} [{diag}] attempts: {attempts}"
    return {
        "queue_size": None,
        "queue_source_url": last_url,
        "queue_fetch_error": full_error,
    }


def _fetch_url(url: str, timeout: int = 2) -> tuple[str, str]:
    """Fetch a URL using HTTP/1.0 to avoid keep-alive issues with Kestrel."""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn._http_vsn = 10
        conn._http_vsn_str = "HTTP/1.0"
        conn.request("GET", path, headers={"Connection": "close", "Accept": "application/json, text/html"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status >= 400:
            return "", f"HTTP {resp.status}"
        # Skip SPA shell responses (Angular/Blazor) — metrics won't be in there.
        if body.strip().startswith("<!doctype") or body.strip().startswith("<!DOCTYPE"):
            return "", f"SPA shell ({len(body)}B)"
        return body, ""
    except Exception as exc:
        return "", f"{host}:{port}: {exc}"


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
    parsed: dict[str, Any] = {"queue_size": None}
    if not text:
        return parsed

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        lower_payload = {str(key).lower(): value for key, value in payload.items()}
        parsed["queue_size"] = _safe_int(lower_payload.get("length", lower_payload.get("queue_size")))
        return parsed

    normalized = unescape(re.sub(r"<[^>]+>", " ", text))
    normalized = re.sub(r"\s+", " ", normalized).strip()

    queue_match = re.search(r"LENGTH\s+([0-9]+)", normalized, re.IGNORECASE)
    if queue_match:
        parsed["queue_size"] = int(queue_match.group(1))

    return parsed


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
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
