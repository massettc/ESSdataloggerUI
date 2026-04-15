from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from app.config import BASE_DIR


class SystemManagerError(RuntimeError):
    pass


def get_system_summary(config: dict[str, Any]) -> dict[str, Any]:
    usage_path = config.get("DISK_USAGE_PATH", "/")
    usage = shutil.disk_usage(usage_path)
    percent = int(round((usage.used / usage.total) * 100)) if usage.total else 0
    return {
        "hostname": socket.gethostname(),
        "disk_total": _format_bytes(usage.total),
        "disk_used": _format_bytes(usage.used),
        "disk_free": _format_bytes(usage.free),
        "disk_percent": percent,
    }


def get_update_status(config: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
    repo_path = Path(config.get("REPO_PATH", BASE_DIR))
    git_bin = config.get("GIT_BIN", "git")
    state, state_message = _read_update_state(config)
    log_excerpt = _read_update_log(config)

    if state == "in-progress" and "Update complete" in log_excerpt:
        state = "success"
        state_message = "Update complete. Refresh the page."
        _write_update_state(config, state, state_message)

    status = {
        "current_branch": "unknown",
        "current_commit": "unknown",
        "update_available": False,
        "behind_by": 0,
        "error": "",
        "state": state,
        "message": state_message,
        "log_excerpt": log_excerpt,
    }

    if not (repo_path / ".git").exists():
        status["error"] = f"Git checkout not found at {repo_path}."
        return status

    if refresh:
        fetch_result = _run_command([git_bin, "-C", str(repo_path), "fetch", "origin", "--prune"], check=False)
        if fetch_result.returncode != 0:
            status["error"] = _command_error(fetch_result, "Unable to contact the git remote")
            _write_update_state(config, "error", status["error"])
            return status
        _write_update_state(config, "idle", "Update check finished.")
        status["state"] = "idle"
        status["message"] = "Update check finished."

    branch_result = _run_command([git_bin, "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
    if branch_result.returncode == 0:
        status["current_branch"] = branch_result.stdout.strip() or "main"
    else:
        status["error"] = _command_error(branch_result, "Unable to read the current git branch")
        return status

    commit_result = _run_command([git_bin, "-C", str(repo_path), "rev-parse", "--short", "HEAD"], check=False)
    if commit_result.returncode == 0:
        status["current_commit"] = commit_result.stdout.strip() or "unknown"

    behind_result = _run_command(
        [git_bin, "-C", str(repo_path), "rev-list", "--count", f"HEAD..origin/{status['current_branch']}"],
        check=False,
    )
    if behind_result.returncode == 0:
        status["behind_by"] = int((behind_result.stdout or "0").strip() or "0")
        status["update_available"] = status["behind_by"] > 0

    status["log_excerpt"] = _read_update_log(config)
    return status


def set_system_hostname(config: dict[str, Any], hostname: str) -> dict[str, Any]:
    hostname = hostname.strip()
    if not hostname:
        return {"success": False, "message": "Hostname is required."}
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", hostname):
        return {
            "success": False,
            "message": "Hostname must use letters, numbers, or hyphens and be 63 characters or fewer.",
        }

    if not _is_linux_target():
        return {"success": False, "message": "Hostname changes are only available on the Pi target device."}

    result = _run_privileged_command(config, [config.get("HOSTNAMECTL_BIN", "hostnamectl"), "set-hostname", hostname], check=False)
    if result.returncode != 0:
        return {"success": False, "message": _command_error(result, "Unable to update the hostname")}

    return {
        "success": True,
        "message": f"Hostname updated to {hostname}. Reboot recommended.",
        "reboot_required": True,
    }


def request_system_reboot(config: dict[str, Any]) -> dict[str, Any]:
    if not _is_linux_target():
        return {"success": False, "message": "Reboot is only available on the Pi target device."}

    result = _run_privileged_command(config, [config.get("SYSTEMCTL_BIN", "systemctl"), "reboot"], check=False)
    if result.returncode != 0:
        return {"success": False, "message": _command_error(result, "Unable to request a reboot")}

    return {"success": True, "message": "Reboot requested. The device may disconnect shortly."}


def run_system_update(config: dict[str, Any]) -> dict[str, Any]:
    if not _is_linux_target():
        return {"success": False, "message": "Update install is only available on the Pi target device."}

    update_script = Path(config.get("UPDATE_SCRIPT", BASE_DIR / "deploy" / "update-from-git.sh"))
    if not update_script.exists():
        return {"success": False, "message": f"Update script not found at {update_script}."}

    repo_status = get_update_status(config, refresh=True)
    ref = repo_status.get("current_branch") or "main"
    log_path = Path(config.get("UPDATE_LOG_PATH", BASE_DIR / "update.log"))

    log_path.parent.mkdir(parents=True, exist_ok=True)
    _write_update_state(config, "in-progress", f"Installing updates from {ref}...")

    bash_bin = config.get("BASH_BIN", "bash")
    command = _build_command_with_optional_sudo(config, [bash_bin, str(update_script), ref], privileged=True)
    with open(log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write("\n=== Update requested from web UI ===\n")
        subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, close_fds=True)

    return {
        "success": True,
        "message": "Update started. The page may disconnect while the service restarts.",
    }


def _is_linux_target() -> bool:
    return os.name == "posix" and os.uname().sysname.lower() == "linux"


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _run_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise SystemManagerError(_command_error(result, "Command failed"))
    return result


def _run_privileged_command(
    config: dict[str, Any],
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = _build_command_with_optional_sudo(config, args, privileged=True)
    return _run_command(command, check=check)


def _build_command_with_optional_sudo(config: dict[str, Any], args: list[str], privileged: bool) -> list[str]:
    command = list(args)
    use_sudo = config.get("USE_SUDO_FOR_SYSTEM", True)
    sudo_bin = config.get("SUDO_BIN", "sudo")
    if privileged and use_sudo and command[0] != sudo_bin:
        command.insert(0, sudo_bin)
        command.insert(1, "-n")
    return command


def _command_error(result: subprocess.CompletedProcess[str], prefix: str) -> str:
    detail = (result.stderr or result.stdout or "unknown error").strip().splitlines()
    if detail:
        return f"{prefix}: {detail[-1]}"
    return prefix


def _write_update_state(config: dict[str, Any], state: str, message: str) -> None:
    state_path = Path(config.get("UPDATE_STATUS_FILE", BASE_DIR / "update-status.txt"))
    state_path.write_text(f"{state}|{message}", encoding="utf-8")


def _read_update_state(config: dict[str, Any]) -> tuple[str, str]:
    state_path = Path(config.get("UPDATE_STATUS_FILE", BASE_DIR / "update-status.txt"))
    if not state_path.exists():
        return "idle", "No recent update activity."

    raw = state_path.read_text(encoding="utf-8").strip()
    if "|" not in raw:
        return "idle", raw or "No recent update activity."

    state, message = raw.split("|", 1)
    return state or "idle", message or "No recent update activity."


def _read_update_log(config: dict[str, Any], lines: int = 12) -> str:
    log_path = Path(config.get("UPDATE_LOG_PATH", BASE_DIR / "update.log"))
    if not log_path.exists():
        return ""

    content = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(content[-lines:])
