import subprocess

from app.services import datalogger_manager


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
