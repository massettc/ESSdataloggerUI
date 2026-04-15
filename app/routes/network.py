from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.auth import login_required
from app.services.network_apply import apply_ethernet_settings, apply_wifi_settings
from app.services.network_manager import (
    ETHERNET_CONNECTION_TYPE,
    NetworkManagerError,
    get_dashboard_state,
    list_connection_profiles,
    scan_wifi_networks,
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

    return render_template("wifi.html", wifi_networks=wifi_networks, state=state)


@network_bp.route("/ethernet", methods=["GET", "POST"])
@login_required
def ethernet_settings():
    if request.method == "POST":
        connection_name = request.form.get("connection_name", "").strip() or None

        try:
            result = apply_ethernet_settings(current_app.config, connection_name=connection_name)
        except NetworkManagerError as exc:
            current_app.logger.exception("ethernet update error")
            flash(str(exc), "error")
            return redirect(url_for("network.ethernet_settings"))

        flash(result["message"], "success" if result["success"] else "error")
        return redirect(url_for("network.ethernet_settings"))

    try:
        state = get_dashboard_state(current_app.config)
        ethernet_profiles = list_connection_profiles(
            current_app.config,
            connection_type=ETHERNET_CONNECTION_TYPE,
            interface_name=current_app.config["ETHERNET_INTERFACE"],
        )
    except NetworkManagerError as exc:
        current_app.logger.exception("ethernet view error")
        flash(str(exc), "error")
        state = _default_state()
        ethernet_profiles = []

    return render_template("ethernet.html", ethernet_profiles=ethernet_profiles, state=state)


def _default_state() -> dict[str, object]:
    return {"hostname": "unavailable", "interfaces": [], "wifi_networks": []}
