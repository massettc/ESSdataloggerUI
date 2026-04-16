import subprocess
from pathlib import Path

from app.services import system_manager


class DummyResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DummyProcess:
    def __init__(self):
        self.pid = 4242
        self.stdout = ["Downloading layer 1\n", "Download complete\n"]

    def wait(self):
        return 0


def test_start_custom_technician_command_creates_running_state(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return DummyProcess()

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

    class DummyTimer:
        def __init__(self, interval, function, args=None, kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args or ()
            self.kwargs = kwargs or {}
            self.daemon = False

        def start(self):
            return None

        def cancel(self):
            return None

    monkeypatch.setattr(system_manager, "_is_linux_target", lambda: True)
    monkeypatch.setattr(system_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(system_manager.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(system_manager.threading, "Timer", DummyTimer)

    config = {
        "TECHNICIAN_OUTPUT_FILE": str(tmp_path / "technician-output.json"),
        "TECHNICIAN_COMMAND_TIMEOUT_SECONDS": 300,
    }

    result = system_manager.start_custom_technician_command(config, "Download image", "docker pull sample")
    saved = (tmp_path / "technician-output.json").read_text(encoding="utf-8")

    assert result["success"] is True
    assert "started" in result["message"].lower()
    assert captured["args"][0] == "/bin/bash"
    assert captured["args"][1] == "-lc"
    assert captured["kwargs"]["stdout"] == subprocess.PIPE
    assert "Download complete" in saved



def test_run_custom_technician_command_uses_linux_shell_env(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return DummyResult(returncode=0, stdout="ok")

    monkeypatch.setattr(system_manager, "_is_linux_target", lambda: True)
    monkeypatch.setattr(system_manager.subprocess, "run", fake_run)

    config = {
        "TECHNICIAN_OUTPUT_FILE": str(tmp_path / "technician-output.json"),
        "COMMAND_TIMEOUT_SECONDS": 15,
    }

    result = system_manager.run_custom_technician_command(config, "Check Docker", "docker ps")

    assert result["success"] is True
    assert captured["args"][0] == "/bin/bash"
    assert captured["args"][1] == "-lc"
    assert captured["args"][2] == "docker ps"
    assert "/usr/bin" in captured["kwargs"]["env"]["PATH"]
    assert captured["kwargs"]["timeout"] == 300


def test_run_custom_technician_command_adds_helpful_127_hint(monkeypatch, tmp_path: Path):
    def fake_run(args, **kwargs):
        return DummyResult(returncode=127, stderr="bash: docker: command not found")

    monkeypatch.setattr(system_manager, "_is_linux_target", lambda: True)
    monkeypatch.setattr(system_manager.subprocess, "run", fake_run)

    config = {
        "TECHNICIAN_OUTPUT_FILE": str(tmp_path / "technician-output.json"),
        "COMMAND_TIMEOUT_SECONDS": 15,
    }

    result = system_manager.run_custom_technician_command(config, "Check Docker", "docker ps")
    saved = (tmp_path / "technician-output.json").read_text(encoding="utf-8")

    assert result["success"] is False
    assert "code 127" in result["message"]
    assert "command was not found" in saved.lower()



def test_run_custom_technician_command_adds_docker_permission_hint(monkeypatch, tmp_path: Path):
    def fake_run(args, **kwargs):
        return DummyResult(
            returncode=1,
            stderr="permission denied while trying to connect to the docker API at unix:///var/run/docker.sock",
        )

    monkeypatch.setattr(system_manager, "_is_linux_target", lambda: True)
    monkeypatch.setattr(system_manager.subprocess, "run", fake_run)

    config = {
        "TECHNICIAN_OUTPUT_FILE": str(tmp_path / "technician-output.json"),
        "COMMAND_TIMEOUT_SECONDS": 15,
    }

    result = system_manager.run_custom_technician_command(config, "Start PLC Reader", "docker run hello-world")
    saved = (tmp_path / "technician-output.json").read_text(encoding="utf-8")

    assert result["success"] is False
    assert "docker access denied" in result["message"].lower()
    assert "docker group" in saved.lower()



def test_start_custom_technician_command_handles_launch_error(monkeypatch, tmp_path: Path):
    def fake_popen(args, **kwargs):
        raise PermissionError("launch failed")

    monkeypatch.setattr(system_manager, "_is_linux_target", lambda: True)
    monkeypatch.setattr(system_manager.subprocess, "Popen", fake_popen)

    config = {
        "TECHNICIAN_OUTPUT_FILE": str(tmp_path / "technician-output.json"),
        "TECHNICIAN_COMMAND_TIMEOUT_SECONDS": 300,
    }

    result = system_manager.start_custom_technician_command(config, "Broken command", "docker pull sample")
    saved = (tmp_path / "technician-output.json").read_text(encoding="utf-8")

    assert result["success"] is False
    assert "unable to start" in result["message"].lower()
    assert "launch failed" in saved.lower()
