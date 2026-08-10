import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def run_alembic(*args):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} fallo:\n{result.stdout}\n{result.stderr}"
    )
    return result


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL no esta definida; copia .env.example a .env")
    return url


@pytest.fixture(scope="session")
def engine(database_url):
    # NullPool: cada conexion es nueva. Las pruebas de RLS dependen de que
    # ninguna sesion anterior deje un tenant_id "pegado" en la conexion.
    return create_engine(database_url, poolclass=NullPool)


@pytest.fixture(scope="session", autouse=True)
def _migrated(database_url):
    # Garantiza que el esquema este al dia antes de que corra cualquier
    # prueba. Las pruebas de test_migrations.py mueven el esquema hacia
    # atras/adelante por su cuenta, pero siempre terminan en head.
    run_alembic("upgrade", "head")


@pytest.fixture
def alembic_runner():
    """Expone run_alembic() a las pruebas sin que tengan que importar
    el modulo conftest directamente (import inestable segun el modo de
    pytest)."""
    return run_alembic
