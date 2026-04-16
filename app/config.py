import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = Path(os.getenv("PI_ADMIN_LOG_PATH", BASE_DIR / "pi-network-admin.log"))
DEFAULT_PASSWORD_HASH_PATH = Path(
    os.getenv("PI_ADMIN_PASSWORD_HASH_FILE", BASE_DIR / "config" / "admin_password.hash")
)


class Config:
    SECRET_KEY = os.getenv("PI_ADMIN_SECRET_KEY", "change-me-before-deploy")
    APP_HOST = os.getenv("PI_ADMIN_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("PI_ADMIN_PORT", "8080"))
    APP_NAME = os.getenv("PI_ADMIN_NAME", "ESS Datalogger UI")
    WIFI_INTERFACE = os.getenv("PI_ADMIN_WIFI_INTERFACE", "wlan0")
    ETHERNET_INTERFACE = os.getenv("PI_ADMIN_ETHERNET_INTERFACE", "eth0")
    PRIMARY_INTERFACE = os.getenv("PI_ADMIN_PRIMARY_INTERFACE", WIFI_INTERFACE)
    BACKUP_INTERFACE = os.getenv("PI_ADMIN_BACKUP_INTERFACE", ETHERNET_INTERFACE)
    PRIMARY_CONNECTION_NAME = os.getenv("PI_ADMIN_PRIMARY_CONNECTION_NAME", "")
    BACKUP_CONNECTION_NAME = os.getenv("PI_ADMIN_BACKUP_CONNECTION_NAME", "")
    NMCLI_BIN = os.getenv("PI_ADMIN_NMCLI_BIN", "nmcli")
    USE_SUDO_FOR_NMCLI = os.getenv("PI_ADMIN_USE_SUDO_FOR_NMCLI", "true").lower() == "true"
    GIT_BIN = os.getenv("PI_ADMIN_GIT_BIN", "git")
    DOCKER_BIN = os.getenv("PI_ADMIN_DOCKER_BIN", "docker")
    BASH_BIN = os.getenv("PI_ADMIN_BASH_BIN", "bash")
    SUDO_BIN = os.getenv("PI_ADMIN_SUDO_BIN", "sudo")
    HOSTNAMECTL_BIN = os.getenv("PI_ADMIN_HOSTNAMECTL_BIN", "hostnamectl")
    SYSTEMCTL_BIN = os.getenv("PI_ADMIN_SYSTEMCTL_BIN", "systemctl")
    USE_SUDO_FOR_SYSTEM = os.getenv("PI_ADMIN_USE_SUDO_FOR_SYSTEM", "true").lower() == "true"
    USE_SUDO_FOR_DOCKER = os.getenv("PI_ADMIN_USE_SUDO_FOR_DOCKER", "true").lower() == "true"
    PORTAINER_CONTAINER_NAME = os.getenv("PI_ADMIN_PORTAINER_CONTAINER_NAME", "portainer")
    PORTAINER_HTTP_PORT = int(os.getenv("PI_ADMIN_PORTAINER_HTTP_PORT", "9000"))
    PORTAINER_HTTPS_PORT = int(os.getenv("PI_ADMIN_PORTAINER_HTTPS_PORT", "9443"))
    PORTAINER_HOSTNAME = os.getenv("PI_ADMIN_PORTAINER_HOSTNAME", "")
    REPO_PATH = os.getenv("PI_ADMIN_REPO_PATH", str(BASE_DIR))
    UPDATE_SCRIPT = os.getenv("PI_ADMIN_UPDATE_SCRIPT", str(BASE_DIR / "deploy" / "update-from-git.sh"))
    UPDATE_LOG_PATH = os.getenv("PI_ADMIN_UPDATE_LOG_PATH", str(BASE_DIR / "update.log"))
    UPDATE_STATUS_FILE = os.getenv("PI_ADMIN_UPDATE_STATUS_FILE", str(BASE_DIR / "update-status.txt"))
    TECHNICIAN_COMMANDS_FILE = os.getenv(
        "PI_ADMIN_TECHNICIAN_COMMANDS_FILE", str(BASE_DIR / "config" / "technician_commands.json")
    )
    TECHNICIAN_OUTPUT_FILE = os.getenv(
        "PI_ADMIN_TECHNICIAN_OUTPUT_FILE", str(BASE_DIR / "technician-output.json")
    )
    DISK_USAGE_PATH = os.getenv("PI_ADMIN_DISK_USAGE_PATH", "/")
    PING_BIN = os.getenv("PI_ADMIN_PING_BIN", "ping")
    COMMAND_TIMEOUT_SECONDS = int(os.getenv("PI_ADMIN_COMMAND_TIMEOUT_SECONDS", "15"))
    VERIFY_TIMEOUT_SECONDS = int(os.getenv("PI_ADMIN_VERIFY_TIMEOUT_SECONDS", "30"))
    VERIFY_POLL_SECONDS = float(os.getenv("PI_ADMIN_VERIFY_POLL_SECONDS", "2"))
    WATCHDOG_ENABLED = os.getenv("PI_ADMIN_WATCHDOG_ENABLED", "true").lower() == "true"
    WATCHDOG_TARGET_HOST = os.getenv("PI_ADMIN_WATCHDOG_TARGET_HOST", "1.1.1.1")
    WATCHDOG_INTERVAL_SECONDS = int(os.getenv("PI_ADMIN_WATCHDOG_INTERVAL_SECONDS", "10"))
    WATCHDOG_PING_TIMEOUT_SECONDS = int(os.getenv("PI_ADMIN_WATCHDOG_PING_TIMEOUT_SECONDS", "2"))
    WATCHDOG_FAILURE_THRESHOLD = int(os.getenv("PI_ADMIN_WATCHDOG_FAILURE_THRESHOLD", "3"))
    WATCHDOG_RECOVERY_THRESHOLD = int(os.getenv("PI_ADMIN_WATCHDOG_RECOVERY_THRESHOLD", "2"))
    PREFER_WLAN_FOR_INTERNET = os.getenv("PI_ADMIN_PREFER_WLAN_FOR_INTERNET", "true").lower() == "true"
    PRIMARY_ROUTE_METRIC = int(os.getenv("PI_ADMIN_PRIMARY_ROUTE_METRIC", "100"))
    BACKUP_ROUTE_METRIC = int(os.getenv("PI_ADMIN_BACKUP_ROUTE_METRIC", "600"))
    AUTH_ENABLED = os.getenv("PI_ADMIN_AUTH_ENABLED", "false").lower() == "true"
    LOG_PATH = str(DEFAULT_LOG_PATH)
    PASSWORD_HASH_FILE = str(DEFAULT_PASSWORD_HASH_PATH)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = os.getenv("PI_ADMIN_SECURE_COOKIE", "false").lower() == "true"
