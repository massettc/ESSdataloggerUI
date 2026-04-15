from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import create_app


@pytest.fixture()
def app(tmp_path: Path):
    password_file = tmp_path / "admin_password.hash"
    password_file.write_text(generate_password_hash("secret123"), encoding="utf-8")
    log_file = tmp_path / "app.log"

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "PASSWORD_HASH_FILE": str(password_file),
            "LOG_PATH": str(log_file),
        }
    )
    return app


@pytest.fixture()
def client(app):
    return app.test_client()
