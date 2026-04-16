from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled():
        session["authenticated"] = True
        return redirect(url_for("network.dashboard"))

    if session.get("authenticated"):
        return redirect(url_for("network.dashboard"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if _is_valid_password(password):
            session["authenticated"] = True
            current_app.logger.info("successful login")
            return redirect(url_for("network.dashboard"))

        current_app.logger.warning("failed login")
        flash("Invalid password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    if _auth_enabled():
        flash("Logged out.", "info")
        return redirect(url_for("auth.login"))
    return redirect(url_for("network.dashboard"))


@auth_bp.route("/health")
def health():
    return {"status": "ok"}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not _auth_enabled():
            return view(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped_view


def _auth_enabled() -> bool:
    return bool(current_app.config.get("AUTH_ENABLED", False))


def _is_valid_password(password: str) -> bool:
    hash_path = Path(current_app.config["PASSWORD_HASH_FILE"])
    if not hash_path.exists():
        current_app.logger.warning("password hash file missing: %s", hash_path)
        return False

    stored_hash = hash_path.read_text(encoding="utf-8").strip()
    if not stored_hash:
        current_app.logger.warning("password hash file empty: %s", hash_path)
        return False

    return check_password_hash(stored_hash, password)
