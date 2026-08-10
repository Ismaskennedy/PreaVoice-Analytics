"""Valida login/logout, proteccion de rutas por sesion, y que solo admin
puede administrar usuarios."""
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.auth import hash_password, verify_password
from webapp.main import app

ROOT = Path(__file__).resolve().parent.parent
DEMO_PASSWORD = "demo1234"  # debe coincidir con scripts/seed.py (SEED_PASSWORD)


def _run_seed():
    result = subprocess.run(
        [sys.executable, "scripts/seed.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, f"scripts/seed.py fallo:\n{result.stdout}\n{result.stderr}"


def test_hash_password_roundtrip():
    hashed = hash_password("una-contrasena-cualquiera")
    assert verify_password("una-contrasena-cualquiera", hashed) is True
    assert verify_password("otra-cosa", hashed) is False


def test_login_with_wrong_password_shows_error():
    _run_seed()
    client = TestClient(app)

    response = client.post(
        "/login", data={"email": "agente-demo@example.com", "password": "no-es-esta"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert "incorrectos" in response.text.lower()


def test_login_with_correct_password_grants_access():
    _run_seed()
    client = TestClient(app)

    login_response = client.post(
        "/login", data={"email": "agente-demo@example.com", "password": DEMO_PASSWORD}, follow_redirects=False
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.text


def test_protected_route_redirects_without_session():
    client = TestClient(app)
    response = client.get("/calls", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logout_clears_session():
    _run_seed()
    client = TestClient(app)
    client.post("/login", data={"email": "agente-demo@example.com", "password": DEMO_PASSWORD})

    client.post("/logout")
    response = client.get("/calls", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_non_admin_cannot_reach_user_management():
    _run_seed()
    client = TestClient(app)
    client.post("/login", data={"email": "agente-demo@example.com", "password": DEMO_PASSWORD})

    response = client.get("/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_admin_can_create_user_who_can_then_log_in():
    _run_seed()
    admin_client = TestClient(app)
    admin_client.post("/login", data={"email": "admin-demo@example.com", "password": DEMO_PASSWORD})

    new_email = f"nuevo-{uuid.uuid4()}@example.com"
    response = admin_client.post(
        "/users",
        data={
            "full_name": "Usuario De Prueba",
            "email": new_email,
            "password": "clave-nueva-123",
            "role": "calidad",
            "external_code": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/users"

    new_client = TestClient(app)
    login_response = new_client.post(
        "/login", data={"email": new_email, "password": "clave-nueva-123"}, follow_redirects=False
    )
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/"
