from werkzeug.security import generate_password_hash

from app import create_app


def test_dashboard_is_accessible_without_login_by_default(client):
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_login_logout_flow_when_auth_is_enabled(tmp_path):
    password_file = tmp_path / "admin_password.hash"
    password_file.write_text(generate_password_hash("secret123"), encoding="utf-8")
    log_file = tmp_path / "app.log"

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "AUTH_ENABLED": True,
            "PASSWORD_HASH_FILE": str(password_file),
            "LOG_PATH": str(log_file),
        }
    )
    client = app.test_client()

    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    response = client.post("/login", data={"password": "wrong"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid password." in response.data

    response = client.post("/login", data={"password": "secret123"})
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    response = client.post("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert b"Logged out." in response.data
