from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
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
    if mqtt_ui_url:
        base_url = mqtt_ui_url.rstrip("/")
        parsed_base = urlparse(base_url)
        scheme = parsed_base.scheme or "http"
        port = parsed_base.port or 8080
        base_urls = [
            f"{scheme}://localhost:{port}",
            f"{scheme}://127.0.0.1:{port}",
            base_url,
        ]
        if parsed_base.hostname:
            base_urls.append(f"{scheme}://{parsed_base.hostname}:{port}")

        candidate_urls: list[str] = []
        for root in dict.fromkeys(base_urls):
            candidate_urls.extend(
                [
                    f"{root}/tools/queue",
                    f"{root}/tools/queue/",
                    f"{root}/tools/Queue",
                    f"{root}/tools/queue-status",
                    f"{root}/tools/QueueStatus",
                    f"{root}/queue-status",
                    f"{root}/QueueStatus",
                    f"{root}/queuestatus",
                    f"{root}/api/queue-status",
                    f"{root}/api/QueueStatus",
                    f"{root}/queue",
                    f"{root}/status",
                    f"{root}/",
                ]
            )

        timeout = max(0.5, min(1.0, float(config.get("VERIFY_POLL_SECONDS", 2))))
        original_netloc = parsed_base.netloc
        headers = {"User-Agent": "ESS-Datalogger-UI/1.0", "Accept": "text/html,application/json"}
        pending_urls = list(dict.fromkeys(candidate_urls))
        seen_urls: set[str] = set()

        last_error = ""
        last_url = ""

        while pending_urls:
            url = pending_urls.pop(0)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            last_url = url

            try:
                request_headers = dict(headers)
                url_parts = urlparse(url)
                if original_netloc and url_parts.hostname in {"localhost", "127.0.0.1"}:
                    request_headers["Host"] = original_netloc
                request = Request(url, headers=request_headers)
                with urlopen(request, timeout=timeout) as response:
                    payload = response.read().decode("utf-8", errors="ignore")
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                last_error = str(exc)
                continue

            parsed = _extract_mqtt_queue_metrics(payload)
            if any(parsed.get(key) is not None for key in ("queue_size", "success_rate", "failure_rate")):
                parsed["queue_source_url"] = url
                parsed["queue_fetch_error"] = ""
                return parsed

            for discovered_url in _discover_mqtt_queue_urls(url, payload):
                if discovered_url not in seen_urls:
                    pending_urls.append(discovered_url)
    else:
        last_error = ""
        last_url = ""

    if docker_bin and container_name:
        container_metrics = _read_mqtt_queue_metrics_from_container(config, docker_bin, container_name)
        if any(container_metrics.get(key) is not None for key in ("queue_size", "success_rate", "failure_rate")):
            return container_metrics
        if container_metrics.get("queue_fetch_error"):
            last_error = container_metrics.get("queue_fetch_error", last_error)
            last_url = container_metrics.get("queue_source_url", last_url)

    return {
        "queue_size": None,
        "success_rate": None,
        "failure_rate": None,
        "success_samples": None,
        "failure_samples": None,
        "queue_source_url": last_url,
        "queue_fetch_error": last_error,
    }


def _read_mqtt_queue_metrics_from_container(config: dict[str, Any], docker_bin: str, container_name: str) -> dict[str, Any]:
    queue_source = f"container://{container_name}/tools/queue"
    commands = [
        [
            docker_bin,
            "exec",
            container_name,
            "python3",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1/tools/queue', timeout=2).read().decode('utf-8', 'ignore'))",
        ],
        [
            docker_bin,
            "exec",
            container_name,
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1/tools/queue', timeout=2).read().decode('utf-8', 'ignore'))",
        ],
    ]

    last_result = None
    for command in commands:
        result = _run_docker_command(config, command, check=False)
        last_result = result
        if result.returncode == 0 and result.stdout.strip():
            parsed = _extract_mqtt_queue_metrics(result.stdout)
            parsed["queue_source_url"] = queue_source
            parsed["queue_fetch_error"] = ""
            return parsed

    result = last_result
    if result is None:
        return {
            "queue_size": None,
            "success_rate": None,
            "failure_rate": None,
            "success_samples": None,
            "failure_samples": None,
            "queue_source_url": queue_source,
            "queue_fetch_error": "Unable to run container queue probe",
        }

    parsed = _extract_mqtt_queue_metrics(result.stdout)
    parsed["queue_source_url"] = queue_source
    parsed["queue_fetch_error"] = ""
    return parsed


def _discover_mqtt_queue_urls(source_url: str, payload: str) -> list[str]:
    if not payload:
        return []

    discovered: list[str] = []
    href_matches = re.findall(r'href=["\']([^"\']+)["\']', payload, re.IGNORECASE)
    script_matches = re.findall(r'["\']([^"\']*(?:queue|status)[^"\']*)["\']', payload, re.IGNORECASE)

    for raw_target in [*href_matches, *script_matches]:
        candidate = (raw_target or "").strip()
        lower_candidate = candidate.lower()
        if not candidate:
            continue
        if "queue" not in lower_candidate and "status" not in lower_candidate:
            continue
        if lower_candidate.startswith("javascript:"):
            continue
        discovered.append(urljoin(source_url, candidate))

    return list(dict.fromkeys(discovered))


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
