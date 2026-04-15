from __future__ import annotations

import os
import subprocess
from typing import Any


class DataloggerManagerError(RuntimeError):
    pass


def get_datalogger_status(config: dict[str, Any], host: str | None = None) -> dict[str, Any]:
    docker_bin = config.get("DOCKER_BIN", "docker")
    container_name = config.get("PORTAINER_CONTAINER_NAME", "portainer")

    status: dict[str, Any] = {
        "docker_available": False,
        "docker_running": False,
        "portainer_installed": False,
        "portainer_running": False,
        "portainer_url": _build_portainer_url(config, host=host),
        "containers": [],
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
        containers.append({"name": name, "image": image, "status": container_status})
        if name == container_name or "portainer" in image.lower():
            status["portainer_installed"] = True
            if container_status.lower().startswith("up"):
                status["portainer_running"] = True

    status["containers"] = containers
    return status


def ensure_portainer(config: dict[str, Any]) -> dict[str, Any]:
    if not _is_linux_target():
        return {"success": False, "message": "Portainer control is only available on the Pi target device."}

    status = get_datalogger_status(config)
    if not status["docker_available"]:
        return {"success": False, "message": "Docker is not available yet. Install Docker on the Pi first."}

    docker_bin = config.get("DOCKER_BIN", "docker")
    container_name = config.get("PORTAINER_CONTAINER_NAME", "portainer")
    http_port = str(config.get("PORTAINER_HTTP_PORT", 9000))
    https_port = str(config.get("PORTAINER_HTTPS_PORT", 9443))

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

    run_result = _run_docker_command(
        config,
        [
            docker_bin,
            "run",
            "-d",
            "--name",
            container_name,
            "--restart=always",
            "-p",
            f"{http_port}:9000",
            "-p",
            f"{https_port}:9443",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            "portainer_data:/data",
            "portainer/portainer-ce:lts",
        ],
        check=False,
    )
    if run_result.returncode != 0:
        return {"success": False, "message": _command_error(run_result, "Unable to install and start Portainer")}

    return {"success": True, "message": "Portainer installed and started successfully."}


def _build_portainer_url(config: dict[str, Any], host: str | None = None) -> str:
    hostname = host or config.get("PORTAINER_HOSTNAME") or "localhost"
    return f"http://{hostname}:{config.get('PORTAINER_HTTP_PORT', 9000)}"


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
    detail = (result.stderr or result.stdout or "unknown error").strip().splitlines()
    if detail:
        return f"{prefix}: {detail[-1]}"
    return prefix


def _is_linux_target() -> bool:
    return os.name == "posix" and os.uname().sysname.lower() == "linux"
