from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.auth import login_required
from app.services.datalogger_manager import DataloggerManagerError, ensure_portainer, get_datalogger_status
from app.services.network_apply import apply_ethernet_settings, apply_wifi_settings
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    get_active_ethernet_connection,
    get_connection_ipv4_config,
    get_dashboard_state,
    list_connection_profiles,
    scan_wifi_networks,
)
from app.services.system_manager import (
    SystemManagerError,
    add_technician_command,
    delete_technician_command,
    get_system_summary,
    get_technician_tools_state,
    get_update_status,
    request_system_reboot,
    run_custom_technician_command,
    run_system_update,
    run_technician_command,
    set_system_hostname,
)


network_bp = Blueprint("network", __name__)


@network_bp.route("/dashboard")
@login_required
def dashboard():
    try:
        state = get_dashboard_state(current_app.config)
    except NetworkManagerError as exc:
        current_app.logger.exception("dashboard state error")
        flash(str(exc), "error")
        state = _default_state()

    return render_template("dashboard.html", state=state)


@network_bp.route("/wifi", methods=["GET", "POST"])
@login_required
def wifi_settings():
    selected_ssid = request.args.get("ssid", "").strip()

    if request.method == "POST":
        ssid = request.form.get("ssid", "").strip()
        password = request.form.get("password", "")
        hidden = request.form.get("hidden") == "on"

        if not ssid:
            flash("SSID is required.", "error")
            return redirect(url_for("network.wifi_settings"))

        try:
            result = apply_wifi_settings(current_app.config, ssid=ssid, password=password, hidden=hidden)
        except NetworkManagerError as exc:
            current_app.logger.exception("wifi update error")
            flash(str(exc), "error")
            return redirect(url_for("network.wifi_settings"))

        if result["success"]:
            flash(result["message"], "success")
        else:
            flash(result["message"], "error")

        return redirect(url_for("network.wifi_settings"))

    try:
        wifi_networks = scan_wifi_networks(current_app.config)
        state = get_dashboard_state(current_app.config)
    except NetworkManagerError as exc:
        current_app.logger.exception("wifi view error")
        flash(str(exc), "error")
        wifi_networks = []
        state = _default_state()

    return render_template(
        "wifi.html",
        wifi_networks=wifi_networks,
        state=state,
        selected_ssid=selected_ssid,
        wifi_interface=current_app.config["WIFI_INTERFACE"],
    )


@network_bp.route("/ethernet", methods=["GET", "POST"])
@login_required
def ethernet_settings():
    if request.method == "POST":
        connection_name = request.form.get("connection_name", "").strip() or None
        ip_method = request.form.get("ip_method", "").strip() or None
        ip_address = request.form.get("ip_address", "").strip()
        ip_prefix = request.form.get("ip_prefix", "").strip()
        gateway = request.form.get("gateway", "").strip()
        dns = request.form.get("dns", "").strip()

        if ip_method == "manual" and not ip_address:
            flash("Static IP address is required for manual Ethernet mode.", "error")
            return redirect(url_for("network.ethernet_settings", profile=connection_name or ""))

        try:
            result = apply_ethernet_settings(
                current_app.config,
                connection_name=connection_name,
                ip_method=ip_method,
                ip_address=ip_address,
                ip_prefix=ip_prefix,
                gateway=gateway,
                dns=dns,
            )
        except NetworkManagerError as exc:
            current_app.logger.exception("ethernet update error")
            flash(str(exc), "error")
            return redirect(url_for("network.ethernet_settings", profile=connection_name or ""))

        flash(result["message"], "success" if result["success"] else "error")
        return redirect(url_for("network.ethernet_settings", profile=connection_name or ""))

    try:
        state = get_dashboard_state(current_app.config)
        ethernet_profiles = list_connection_profiles(
            current_app.config,
            connection_type=ETHERNET_CONNECTION_TYPE,
            interface_name=current_app.config["ETHERNET_INTERFACE"],
        )
        active_ethernet = get_active_ethernet_connection(current_app.config)
        selected_profile = request.args.get("profile", "").strip() or (
            active_ethernet["name"] if active_ethernet else (ethernet_profiles[0]["name"] if ethernet_profiles else "")
        )
        ipv4_config = (
            get_connection_ipv4_config(current_app.config, selected_profile)
            if selected_profile
            else _default_ipv4_config()
        )
    except NetworkManagerError as exc:
        current_app.logger.exception("ethernet view error")
        flash(str(exc), "error")
        state = _default_state()
        ethernet_profiles = []
        active_ethernet = None
        selected_profile = ""
        ipv4_config = _default_ipv4_config()

    return render_template(
        "ethernet.html",
        ethernet_profiles=ethernet_profiles,
        state=state,
        active_ethernet=active_ethernet,
        selected_profile=selected_profile,
        ipv4_config=ipv4_config,
    )


@network_bp.route("/datalogger", methods=["GET", "POST"])
@login_required
def datalogger():
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        try:
            if action == "portainer":
                result = ensure_portainer(current_app.config)
            else:
                result = {"success": False, "message": "Unknown datalogger action."}
        except DataloggerManagerError as exc:
            current_app.logger.exception("datalogger action error")
            flash(str(exc), "error")
            return redirect(url_for("network.datalogger"))

        flash(result["message"], "success" if result["success"] else "error")
        return redirect(url_for("network.datalogger"))

    try:
        datalogger_status = get_datalogger_status(current_app.config, host=request.host.split(":")[0])
    except DataloggerManagerError as exc:
        current_app.logger.exception("datalogger view error")
        flash(str(exc), "error")
        datalogger_status = _default_datalogger_status()

    return render_template("datalogger.html", datalogger_status=datalogger_status)


@network_bp.route("/datalogger/status")
@login_required
def datalogger_status_api():
    try:
        return get_datalogger_status(current_app.config, host=request.host.split(":")[0])
    except DataloggerManagerError as exc:
        current_app.logger.exception("datalogger status api error")
        status = _default_datalogger_status()
        status["error"] = str(exc)
        return status


@network_bp.route("/system", methods=["GET", "POST"])
@login_required
def system_settings():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        try:
            if action == "hostname":
                result = set_system_hostname(current_app.config, request.form.get("hostname", ""))
            elif action == "reboot":
                result = request_system_reboot(current_app.config)
            elif action == "check_updates":
                update_status = get_update_status(current_app.config, refresh=True)
                if update_status["error"]:
                    flash(update_status["error"], "error")
                elif update_status["update_available"]:
                    flash(
                        f"{update_status['behind_by']} update(s) available on {update_status['current_branch']}.",
                        "info",
                    )
                else:
                    flash("System is already up to date.", "success")
                return redirect(url_for("network.system_settings"))
            elif action == "update":
                result = run_system_update(current_app.config)
            else:
                result = {"success": False, "message": "Unknown system action."}
        except SystemManagerError as exc:
            current_app.logger.exception("system action error")
            flash(str(exc), "error")
            return redirect(url_for("network.system_settings"))

        flash(result["message"], "success" if result["success"] else "error")
        if result.get("reboot_required"):
            flash("A reboot is recommended to finish applying the new hostname.", "info")
        return redirect(url_for("network.system_settings"))

    try:
        system = get_system_summary(current_app.config)
        update_status = get_update_status(current_app.config)
    except SystemManagerError as exc:
        current_app.logger.exception("system view error")
        flash(str(exc), "error")
        system = _default_system_summary()
        update_status = _default_update_status()

    return render_template("system.html", system=system, update_status=update_status)


@network_bp.route("/tools", methods=["GET", "POST"])
@login_required
def technician_tools():
    if request.method == "POST":
        action = request.form.get("action", "").strip()

        try:
            if action == "run_command":
                result = run_technician_command(current_app.config, request.form.get("command_id", ""))
            elif action == "run_custom":
                result = run_custom_technician_command(
                    current_app.config,
                    request.form.get("custom_label", "Custom command"),
                    request.form.get("custom_command", ""),
                )
            elif action == "add_command":
                result = add_technician_command(
                    current_app.config,
                    request.form.get("label", ""),
                    request.form.get("command", ""),
                    request.form.get("description", ""),
                    request.form.get("confirm") == "on",
                )
            elif action == "delete_command":
                result = delete_technician_command(current_app.config, request.form.get("command_id", ""))
            else:
                result = {"success": False, "message": "Unknown technician action."}
        except SystemManagerError as exc:
            current_app.logger.exception("technician tools action error")
            flash(str(exc), "error")
            return redirect(url_for("network.technician_tools"))

        flash(result["message"], "success" if result["success"] else "error")
        return redirect(url_for("network.technician_tools"))

    try:
        tools_state = get_technician_tools_state(current_app.config)
    except SystemManagerError as exc:
        current_app.logger.exception("technician tools view error")
        flash(str(exc), "error")
        tools_state = _default_technician_tools_state()

    return render_template("technician_tools.html", tools_state=tools_state)


def _default_state() -> dict[str, object]:
    return {"hostname": "unavailable", "interfaces": [], "wifi_networks": [], "internet_access": False}


def _default_ipv4_config() -> dict[str, str]:
    return {"method": "auto", "address": "", "prefix": "24", "gateway": "", "dns": ""}


def _default_system_summary() -> dict[str, object]:
    return {"hostname": "unavailable", "disk_total": "0 GB", "disk_used": "0 GB", "disk_free": "0 GB", "disk_percent": 0}


def _default_update_status() -> dict[str, object]:
    return {
        "current_branch": "unknown",
        "current_commit": "unknown",
        "update_available": False,
        "behind_by": 0,
        "error": "",
        "state": "idle",
        "message": "No recent update activity.",
        "log_excerpt": "",
    }


def _default_technician_tools_state() -> dict[str, object]:
    return {"commands": [], "last_result": None, "error": ""}


def _default_datalogger_status() -> dict[str, object]:
    return {
        "docker_available": False,
        "docker_running": False,
        "portainer_installed": False,
        "portainer_running": False,
        "portainer_url": "",
        "mqtt_ui_url": "",
        "active_logger": "No Logger Running",
        "warnings": [],
        "mqtt_logger": {
            "name": "opsviewer2-edge",
            "summary": "No recent activity",
            "last_activity_text": "Unknown",
            "last_push_age_seconds": None,
            "last_push_label": "Waiting for data",
            "status_class": "status-neutral",
            "device_id": "",
            "channel_count": None,
            "error": "",
        },
        "plc_logger": {
            "name": "plcreader",
            "summary": "No recent activity",
            "last_activity_text": "Unknown",
            "last_push_age_seconds": None,
            "last_push_label": "Waiting for data",
            "status_class": "status-neutral",
            "measurements": None,
            "queue_size": None,
            "error": "",
        },
        "containers": [],
        "error": "",
    }
