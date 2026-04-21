from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from datetime import datetime
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

    hostname_script = _get_hostname_update_script(config)
    if not hostname_script.exists():
        return {"success": False, "message": f"Hostname update script not found at {hostname_script}."}

    result = _run_privileged_command(
        config,
        [_get_privileged_bash_bin(config), str(hostname_script), hostname],
        check=False,
    )
    if result.returncode != 0:
        return {"success": False, "message": _command_error(result, "Unable to update the hostname")}

    return {
        "success": True,
        "message": f"Hostname updated to {hostname}. Reboot recommended.",
        "reboot_required": True,
    }


def _get_hostname_update_script(config: dict[str, Any]) -> Path:
    repo_path = Path(config.get("REPO_PATH", BASE_DIR))
    return repo_path / "deploy" / "set-hostname.sh"


def _get_privileged_bash_bin(config: dict[str, Any]) -> str:
    configured = str(config.get("BASH_BIN", "") or "").strip()
    if configured.startswith("/"):
        return configured
    return "/bin/bash"


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


def get_technician_tools_state(config: dict[str, Any]) -> dict[str, Any]:
    last_result = _read_technician_output(config)
    if last_result and last_result.get("status") == "running" and not _is_process_running(last_result.get("pid")):
        output = str(last_result.get("output", "")).strip()
        if "timed out" not in output.lower():
            output = f"{output}\n\nProcess ended unexpectedly.".strip()
            last_result = {
                **last_result,
                "status": "error",
                "exit_code": -1,
                "output": output,
                "finished_at": last_result.get("finished_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _write_technician_output(config, last_result)

    json_files = _get_allowed_json_files(config)
    selected_json_file = str(config.get("SELECTED_JSON_FILE", "")).strip()
    if not selected_json_file and json_files:
        selected_json_file = str(json_files[0].get("id", ""))
    json_editor_content, json_editor_error = _read_selected_json_content(config, selected_json_file, json_files)

    return {
        "commands": _load_technician_commands(config),
        "last_result": last_result,
        "json_files": json_files,
        "selected_json_file": selected_json_file,
        "json_editor_content": json_editor_content,
        "json_editor_error": json_editor_error,
        "error": "",
    }


def add_technician_command(
    config: dict[str, Any],
    label: str,
    command: str,
    description: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    label = label.strip()
    command = command.strip()
    description = description.strip()

    if not label or not command:
        return {"success": False, "message": "Button label and command are required."}

    commands = _load_technician_commands(config)
    command_id = _build_unique_command_id(commands, label)
    commands.append(
        {
            "id": command_id,
            "label": label,
            "command": command,
            "description": description,
            "confirm": bool(confirm),
            "builtin": False,
        }
    )
    _save_technician_commands(config, commands)
    return {"success": True, "message": f"Saved button {label}."}


def delete_technician_command(config: dict[str, Any], command_id: str) -> dict[str, Any]:
    command_id = command_id.strip()
    commands = _load_technician_commands(config)
    remaining = [item for item in commands if item.get("id") != command_id]

    if len(remaining) == len(commands):
        return {"success": False, "message": "That saved button was not found."}

    _save_technician_commands(config, remaining)
    return {"success": True, "message": "Saved button removed."}


def save_technician_json_file(config: dict[str, Any], file_id: str, content: str) -> dict[str, Any]:
    file_id = file_id.strip()
    allowed_files = _get_allowed_json_files(config)
    selected = next((item for item in allowed_files if item.get("id") == file_id), None)
    if not selected:
        return {"success": False, "message": "That file is not available in the editor."}

    raw_content = content.rstrip() + "\n"
    if not raw_content.strip():
        return {"success": False, "message": "Editor content cannot be empty."}

    output_path = Path(str(selected.get("path", "")))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    editor_type = str(selected.get("editor_type", "json"))
    if editor_type == "json":
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            return {"success": False, "message": f"Please enter valid JSON before saving: {exc.msg}."}
        output_path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
        return {"success": True, "message": f"Saved JSON file {output_path.name}."}

    output_path.write_text(raw_content, encoding="utf-8")
    return {"success": True, "message": f"Saved file {output_path.name}."}


def start_technician_command(config: dict[str, Any], command_id: str) -> dict[str, Any]:
    command_id = command_id.strip()
    commands = _load_technician_commands(config)
    selected = next((item for item in commands if item.get("id") == command_id), None)
    if not selected:
        return {"success": False, "message": "Saved button was not found."}

    return start_custom_technician_command(config, selected.get("label", "Saved command"), selected.get("command", ""))


def start_custom_technician_command(config: dict[str, Any], label: str, command: str) -> dict[str, Any]:
    label = label.strip() or "Custom command"
    command = command.strip()
    if not command:
        return {"success": False, "message": "A command is required."}

    if command.startswith("sudo "):
        _write_technician_output(
            config,
            {
                "command_label": label,
                "command": command,
                "status": "error",
                "exit_code": 1,
                "output": "This page runs commands without sudo. Remove sudo from the saved button and use a full binary path like /usr/bin/docker.",
                "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return {"success": False, "message": "This page runs commands without sudo. Remove sudo from the button."}

    active = _read_technician_output(config)
    if active and active.get("status") == "running" and _is_process_running(active.get("pid")):
        return {"success": False, "message": "Another command is already running. Wait for it to finish first."}

    working_directory = str(config.get("REPO_PATH", BASE_DIR))
    command_env = _build_technician_command_env(config)
    timeout = int(config.get("TECHNICIAN_COMMAND_TIMEOUT_SECONDS", 300))

    if _is_linux_target():
        bash_bin = str(config.get("BASH_BIN", "/bin/bash") or "/bin/bash")
        run_args: Any = [bash_bin, "-lc", command]
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "cwd": working_directory,
            "env": command_env,
            "bufsize": 1,
        }
    else:
        run_args = command
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "cwd": working_directory,
            "env": command_env,
            "bufsize": 1,
            "shell": True,
        }

    try:
        process = subprocess.Popen(run_args, **popen_kwargs)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        payload = {
            "command_label": label,
            "command": command,
            "status": "error",
            "exit_code": 1,
            "output": f"Unable to start the command: {exc}",
            "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": None,
        }
        _write_technician_output(config, payload)
        return {"success": False, "message": f"Unable to start {label}."}

    payload = {
        "command_label": label,
        "command": command,
        "status": "running",
        "exit_code": None,
        "output": "",
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "pid": process.pid,
    }
    _write_technician_output(config, payload)

    timeout_timer = threading.Timer(timeout, _terminate_technician_process, args=(config, process, payload, timeout))
    timeout_timer.daemon = True
    timeout_timer.start()
    threading.Thread(
        target=_stream_technician_process,
        args=(config, process, payload, timeout_timer),
        daemon=True,
    ).start()

    return {"success": True, "message": f"Started {label}. Live output is shown below."}


def run_technician_command(config: dict[str, Any], command_id: str) -> dict[str, Any]:
    command_id = command_id.strip()
    commands = _load_technician_commands(config)
    selected = next((item for item in commands if item.get("id") == command_id), None)
    if not selected:
        return {"success": False, "message": "Saved button was not found."}

    return run_custom_technician_command(config, selected.get("label", "Saved command"), selected.get("command", ""))


def run_custom_technician_command(config: dict[str, Any], label: str, command: str) -> dict[str, Any]:
    label = label.strip() or "Custom command"
    command = command.strip()
    if not command:
        return {"success": False, "message": "A command is required."}

    if command.startswith("sudo "):
        _write_technician_output(
            config,
            {
                "command_label": label,
                "command": command,
                "exit_code": 1,
                "output": "This page runs commands without sudo. Remove sudo from the saved button and use a full binary path like /usr/bin/docker.",
                "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return {"success": False, "message": "This page runs commands without sudo. Remove sudo from the button."}

    timeout = int(config.get("TECHNICIAN_COMMAND_TIMEOUT_SECONDS", 300))
    working_directory = str(config.get("REPO_PATH", BASE_DIR))
    command_env = _build_technician_command_env(config)

    if _is_linux_target():
        bash_bin = str(config.get("BASH_BIN", "/bin/bash") or "/bin/bash")
        run_args: Any = [bash_bin, "-lc", command]
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": timeout,
            "cwd": working_directory,
            "env": command_env,
        }
    else:
        run_args = command
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "shell": True,
            "check": False,
            "timeout": timeout,
            "cwd": working_directory,
            "env": command_env,
        }

    try:
        result = subprocess.run(run_args, **run_kwargs)
        combined_output = "\n".join(part.strip() for part in [result.stdout or "", result.stderr or ""] if part.strip())
        combined_output, docker_permission_denied = _decorate_technician_output(command, combined_output, result.returncode)

        payload = {
            "command_label": label,
            "command": command,
            "exit_code": result.returncode,
            "output": combined_output or "(no output)",
            "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_technician_output(config, payload)

        if result.returncode == 0:
            return {"success": True, "message": f"Finished: {label}."}
        if docker_permission_denied:
            return {"success": False, "message": "Docker access denied for the app service user."}
        return {"success": False, "message": f"{label} exited with code {result.returncode}."}
    except subprocess.TimeoutExpired as exc:
        timed_output = "\n".join(
            part.strip()
            for part in [
                (exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else exc.stdout) or "",
                (exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else exc.stderr) or "",
                f"Command timed out after {timeout} seconds.",
            ]
            if part and part.strip()
        )
        timeout_note = _build_technician_timeout_note(command, timeout)
        _write_technician_output(
            config,
            {
                "command_label": label,
                "command": command,
                "status": "error",
                "exit_code": -1,
                "output": timed_output or timeout_note,
                "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return {"success": False, "message": f"{label} timed out after {timeout} seconds."}


def _is_linux_target() -> bool:
    return os.name == "posix" and os.uname().sysname.lower() == "linux"


def _is_process_running(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


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


def _build_technician_command_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    if _is_linux_target():
        default_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env["PATH"] = str(config.get("TECHNICIAN_COMMAND_PATH", default_path) or default_path)
        env.setdefault("HOME", str(Path.home()))
    return env


def _stream_technician_process(
    config: dict[str, Any],
    process: subprocess.Popen[str],
    payload: dict[str, Any],
    timeout_timer: threading.Timer,
) -> None:
    output = str(payload.get("output", ""))
    try:
        if process.stdout is not None:
            for line in process.stdout:
                output = _append_technician_output(output, line)
                _write_technician_output(config, {**payload, "status": "running", "exit_code": None, "output": output})

        exit_code = process.wait()
    finally:
        timeout_timer.cancel()

    latest = _read_technician_output(config) or payload
    if latest.get("status") == "error" and latest.get("exit_code") == -1 and "timed out" in str(latest.get("output", "")).lower():
        return

    final_output, _ = _decorate_technician_output(str(payload.get("command", "")), output.strip(), exit_code)
    _write_technician_output(
        config,
        {
            **payload,
            "status": "success" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "output": final_output or "(no output)",
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def _terminate_technician_process(
    config: dict[str, Any],
    process: subprocess.Popen[str],
    payload: dict[str, Any],
    timeout: int,
) -> None:
    if process.poll() is not None:
        return

    try:
        process.kill()
    except OSError:
        pass

    current = _read_technician_output(config) or payload
    output = _append_technician_output(str(current.get("output", "")), "\n" + _build_technician_timeout_note(str(payload.get("command", "")), timeout))
    _write_technician_output(
        config,
        {
            **current,
            "status": "error",
            "exit_code": -1,
            "output": output,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def _append_technician_output(existing: str, chunk: str, max_chars: int = 60000) -> str:
    combined = f"{existing}{chunk.replace(chr(13), '')}"
    if len(combined) > max_chars:
        combined = combined[-max_chars:]
    return combined


def _build_technician_timeout_note(command: str, timeout: int) -> str:
    note = f"Command timed out after {timeout} seconds."
    if "docker" in command.lower():
        note += " Large image pulls may need a higher technician timeout or a separate docker pull step."
    return note


def _decorate_technician_output(command: str, output: str, exit_code: int) -> tuple[str, bool]:
    lower_output = output.lower()
    docker_permission_denied = "permission denied" in lower_output and "docker.sock" in lower_output

    if exit_code == 127:
        output = "\n".join(
            part
            for part in [
                output or "Command returned code 127.",
                "Hint: the command was not found for the app user. Try a full path such as /usr/bin/docker and avoid sudo in this page.",
            ]
            if part
        )
    elif docker_permission_denied:
        output = "\n".join(
            part
            for part in [
                output or "Docker access was denied.",
                "Hint: add pi-network-admin to the docker group and restart the pi-network-admin service.",
            ]
            if part
        )

    return output, docker_permission_denied


def _get_allowed_json_files(config: dict[str, Any]) -> list[dict[str, str]]:
    repo_path = Path(str(config.get("REPO_PATH", BASE_DIR)))
    configured_paths = _parse_json_editor_paths(config)

    if not configured_paths:
        default_config_dir = repo_path / "config"
        if default_config_dir.exists():
            configured_paths = sorted(str(path) for path in default_config_dir.glob("*.json"))

    for default_path in _default_json_editor_paths(config):
        if default_path not in configured_paths:
            configured_paths.append(default_path)

    files: list[dict[str, str]] = []
    for raw_path in configured_paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = repo_path / candidate

        if candidate.is_dir():
            for path in sorted(candidate.glob("*.json")):
                files.append(_build_json_file_entry(path, len(files)))
            continue

        if candidate.suffix.lower() not in {".json", ".env"} and candidate.name.lower() != "app.env":
            continue
        files.append(_build_json_file_entry(candidate, len(files)))

    unique_files: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in files:
        path_key = str(item.get("path", ""))
        if path_key in seen:
            continue
        seen.add(path_key)
        unique_files.append(item)
    return unique_files


def _parse_json_editor_paths(config: dict[str, Any]) -> list[str]:
    raw_value = str(config.get("JSON_EDITOR_PATHS", "") or "")
    if not raw_value:
        return []

    normalized = raw_value.replace("\r", "\n")
    separators = ["\n", ","]
    for separator in separators:
        normalized = normalized.replace(separator, os.pathsep)
    return [part.strip() for part in normalized.split(os.pathsep) if part.strip()]


def _default_json_editor_paths(config: dict[str, Any]) -> list[str]:
    return [
        "/var/usr/plcreader/settings.json",
        str(config.get("PLC_ALARM_CONFIG_FILE") or "/etc/pi-network-admin/plc_alarm.json"),
        "/etc/pi-network-admin/app.env",
    ]


def _build_json_file_entry(path: Path, index: int) -> dict[str, str]:
    base = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-") or "json-file"
    file_id = base if index == 0 else f"{base}-{index + 1}"
    editor_type = "json" if path.suffix.lower() == ".json" else "text"
    return {"id": file_id, "label": path.name, "path": str(path), "editor_type": editor_type}


def _read_selected_json_content(
    config: dict[str, Any],
    selected_json_file: str,
    json_files: list[dict[str, str]],
) -> tuple[str, str]:
    if not selected_json_file:
        return "", ""

    selected = next((item for item in json_files if item.get("id") == selected_json_file), None)
    if not selected:
        return "", "Selected JSON file was not found."

    json_path = Path(str(selected.get("path", "")))
    if not json_path.exists():
        return "{}\n", "The JSON file does not exist yet. Saving will create it."

    raw_content = json_path.read_text(encoding="utf-8")
    if json_path.suffix.lower() == ".json":
        try:
            parsed = json.loads(raw_content)
            return json.dumps(parsed, indent=2), ""
        except json.JSONDecodeError as exc:
            return raw_content, f"This file is not currently valid JSON: {exc.msg}."

    return raw_content, ""



def _load_technician_commands(config: dict[str, Any]) -> list[dict[str, Any]]:
    commands_path = Path(config.get("TECHNICIAN_COMMANDS_FILE", BASE_DIR / "config" / "technician_commands.json"))
    if not commands_path.exists():
        return _default_technician_commands()

    try:
        payload = json.loads(commands_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_technician_commands()

    if not isinstance(payload, list):
        return _default_technician_commands()

    commands: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        command = str(entry.get("command", "")).strip()
        if not label or not command:
            continue
        commands.append(
            {
                "id": str(entry.get("id", "")).strip() or _build_unique_command_id(commands, label),
                "label": label,
                "command": command,
                "description": str(entry.get("description", "")).strip(),
                "confirm": bool(entry.get("confirm", False)),
                "builtin": bool(entry.get("builtin", False)),
            }
        )

    return commands or _default_technician_commands()


def _save_technician_commands(config: dict[str, Any], commands: list[dict[str, Any]]) -> None:
    commands_path = Path(config.get("TECHNICIAN_COMMANDS_FILE", BASE_DIR / "config" / "technician_commands.json"))
    commands_path.parent.mkdir(parents=True, exist_ok=True)
    commands_path.write_text(json.dumps(commands, indent=2), encoding="utf-8")


def _read_technician_output(config: dict[str, Any]) -> dict[str, Any] | None:
    output_path = Path(config.get("TECHNICIAN_OUTPUT_FILE", BASE_DIR / "technician-output.json"))
    if not output_path.exists():
        return None

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return payload if isinstance(payload, dict) else None


def _write_technician_output(config: dict[str, Any], payload: dict[str, Any]) -> None:
    output_path = Path(config.get("TECHNICIAN_OUTPUT_FILE", BASE_DIR / "technician-output.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(output_path)


def _build_unique_command_id(commands: list[dict[str, Any]], label: str) -> str:
    base_id = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "custom-command"
    existing_ids = {str(item.get("id", "")).strip() for item in commands}
    if base_id not in existing_ids:
        return base_id

    suffix = 2
    while f"{base_id}-{suffix}" in existing_ids:
        suffix += 1
    return f"{base_id}-{suffix}"


def _default_technician_commands() -> list[dict[str, Any]]:
    if _is_linux_target():
        return [
            {
                "id": "stop-plcreader",
                "label": "Stop plcreader",
                "command": "docker stop plcreader",
                "description": ".",
                "confirm": False,
                "builtin": False,
            },
            {
                "id": "start-plcreader",
                "label": "Start plcreader",
                "command": "docker start plcreader",
                "description": ".",
                "confirm": False,
                "builtin": False,
            },
            {
                "id": "remove-plcreader",
                "label": "Remove plcreader",
                "command": "docker rm plcreader",
                "description": ".",
                "confirm": False,
                "builtin": False,
            },
            {
                "id": "download-plcreader",
                "label": "Download plcreader",
                "command": "/usr/bin/docker run -d --name plcreader --restart unless-stopped -v /var/usr/plcreader:/var/usr/plcreader opsviewer2/ultralight:r1363 dotnet EmeraldSurf.OpsViewer.PlcReader.dll startlogging",
                "description": "version r1363",
                "confirm": False,
                "builtin": False,
            },
            {
                "id": "attach-plcreader",
                "label": "Attach plcreader",
                "command": "docker attach plcreader",
                "description": ".",
                "confirm": False,
                "builtin": False,
            },
            {
                "id": "prune-plcreader",
                "label": "Prune plcreader",
                "command": "docker system prune -fa",
                "description": ".",
                "confirm": False,
                "builtin": False,
            },
        ]

    return [
        {
            "id": "show-hostname",
            "label": "Show hostname",
            "command": "hostname",
            "description": "Display the current machine hostname.",
            "confirm": False,
            "builtin": True,
        },
        {
            "id": "show-user",
            "label": "Show current user",
            "command": "whoami",
            "description": "Confirm which account the app is running under.",
            "confirm": False,
            "builtin": True,
        },
        {
            "id": "show-ipconfig",
            "label": "Show network config",
            "command": "ipconfig",
            "description": "Display current adapter and IP information.",
            "confirm": False,
            "builtin": True,
        },
    ]
