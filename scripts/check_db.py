"""make check-db: confirma que se puede conectar a Postgres y que esquemas ya existen."""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main() -> int:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL no esta definida. Copia .env.example a .env y llena la cadena de conexion.")
        return 1

    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            print(f"Conexion OK: {version}")

            schemas = conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name IN ('app', 'ops', 'audit') ORDER BY schema_name"
                )
            ).scalars().all()
            if schemas:
                print(f"Esquemas encontrados: {', '.join(schemas)}")
            else:
                print("Los esquemas app/ops/audit todavia no existen. Corre 'make migrate'.")
    except Exception as exc:  # noqa: BLE001 - queremos reportar cualquier fallo de conexion
        print(f"No se pudo conectar: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
