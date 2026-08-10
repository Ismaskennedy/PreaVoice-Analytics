import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no esta definida. Copia .env.example a .env y llena la cadena de conexion."
    )

# El login ya existe (quien eres), pero el tenant sigue fijo (para que
# organizacion trabajas) porque el v1 solo tiene un tenant real. El dia que
# haya mas de uno, esto se resuelve al momento de loguearse, no antes.
TENANT_SLUG = os.environ.get("TENANT_SLUG", "demo")

# Firma las cookies de sesion del login. En .env.example viene vacia a
# proposito: cada quien debe generar la suya (no hay una buena por default
# que sea segura). Si no esta definida, se genera una nueva cada vez que
# arranca el servidor -- funciona, pero cierra la sesion de todos al reiniciar.
SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()

RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", "./data/recordings"))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# No se valida aqui (a diferencia de DATABASE_URL) porque las pruebas y
# scripts que no tocan IA no deberian necesitarla. Se valida al usarla,
# en webapp/openai_client.py.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TRANSCRIPTION_MODEL = os.environ.get("TRANSCRIPTION_MODEL", "whisper-1")
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "gpt-4o-mini")
