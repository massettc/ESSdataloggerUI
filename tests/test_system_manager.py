from pathlib import Path

from app.services import system_manager


class DummyResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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
