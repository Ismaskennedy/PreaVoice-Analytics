"""Prueba de extremo a extremo de la pantalla de carga manual: subir un
archivo debe crear la llamada con su client_id congelado (regla #2) y
aparecer en la lista. Requiere sesion iniciada (login), como cualquier
pantalla protegida."""
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from webapp.main import app

ROOT = Path(__file__).resolve().parent.parent
DEMO_PASSWORD = "demo1234"  # debe coincidir con scripts/seed.py (SEED_PASSWORD)


def _run_seed():
    result = subprocess.run(
        [sys.executable, "scripts/seed.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"scripts/seed.py fallo:\n{result.stdout}\n{result.stderr}"


def _logged_in_client(email="agente-demo@example.com") -> TestClient:
    client = TestClient(app)
    response = client.post("/login", data={"email": email, "password": DEMO_PASSWORD}, follow_redirects=False)
    assert response.status_code == 303, f"login fallo para {email}: {response.text}"
    return client


def test_upload_creates_call_and_shows_in_list(engine):
    _run_seed()
    client = _logged_in_client()

    filename = f"prueba-{uuid.uuid4()}.mp3"
    response = client.post(
        "/calls/upload",
        files={"file": (filename, b"contenido de audio falso", "audio/mpeg")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert filename in response.text

    with engine.connect() as conn:
        tenant_id = conn.execute(text("SELECT id FROM app.tenants WHERE slug = 'demo'")).scalar_one()
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
        row = conn.execute(
            text(
                "SELECT cl.code AS client_code "
                "FROM app.recordings r "
                "JOIN app.calls c ON c.id = r.call_id "
                "JOIN app.clients cl ON cl.id = c.client_id "
                "WHERE r.original_filename = :filename"
            ),
            {"filename": filename},
        ).mappings().one()

    assert row["client_code"] == "demo"


def test_upload_without_login_redirects_to_login():
    _run_seed()
    client = TestClient(app)

    response = client.post(
        "/calls/upload",
        files={"file": ("sin-sesion.mp3", b"contenido", "audio/mpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
