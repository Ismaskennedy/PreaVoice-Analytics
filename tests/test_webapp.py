"""Prueba de extremo a extremo de la pantalla de carga manual: subir un
archivo debe crear la llamada con su client_id congelado (regla #2) y
aparecer en la lista. Requiere sesion iniciada (login) Y rol admin --
/calls/upload se restringio a admin (antes cualquier gestor la veia)."""
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
    client = _logged_in_client(email="admin-demo@example.com")

    filename = f"prueba-{uuid.uuid4()}.mp3"
    response = client.post(
        "/calls/upload",
        files={"file": (filename, b"contenido de audio falso", "audio/mpeg")},
        follow_redirects=True,
    )

    # La lista ya no muestra el nombre del archivo (regla nueva: se muestra
    # el telefono del deudor, para no exponer el nombre crudo del .mp3). Sin
    # telefono en una carga manual, la tarjeta dice "Sin telefono".
    assert response.status_code == 200
    assert "Sin teléfono" in response.text

    with engine.connect() as conn:
        tenant_id = conn.execute(text("SELECT id FROM app.tenants WHERE slug = 'demo'")).scalar_one()
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})

        # /calls/upload es solo-admin: sin selector de agente en pantalla
        # todavia (pendiente real, ver CLAUDE.md), la llamada se asigna al
        # gestor que _agent_id() elija -- se recalcula aqui con la misma
        # consulta en vez de asumir cual gestor es, para no depender de que
        # otros gestores existan en esta base compartida.
        expected_agent_id = conn.execute(
            text(
                "SELECT u.id FROM app.users u "
                "JOIN app.user_roles ur ON ur.user_id = u.id "
                "JOIN app.roles r ON r.id = ur.role_id "
                "WHERE r.code = 'agente' ORDER BY u.full_name LIMIT 1"
            )
        ).scalar_one()
        expected_client_id = conn.execute(
            text("SELECT client_id FROM app.agent_assignments WHERE user_id = :agent_id"),
            {"agent_id": expected_agent_id},
        ).scalar_one()

        row = conn.execute(
            text(
                "SELECT c.client_id, c.agent_id "
                "FROM app.recordings r "
                "JOIN app.calls c ON c.id = r.call_id "
                "WHERE r.original_filename = :filename"
            ),
            {"filename": filename},
        ).mappings().one()

    assert row["agent_id"] == expected_agent_id
    assert row["client_id"] == expected_client_id


def test_non_admin_cannot_upload():
    _run_seed()
    client = _logged_in_client(email="calidad-demo@example.com")

    response = client.post(
        "/calls/upload",
        files={"file": ("no-deberia-subir.mp3", b"contenido", "audio/mpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


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
