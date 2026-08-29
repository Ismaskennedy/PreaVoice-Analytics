"""Borra llamadas/grabaciones/transcripciones/analisis de un cliente
especifico -- pensado para limpiar un lote de prueba (como el disco de 2M)
y volver a importar/transcribir desde cero con ajustes nuevos, sin tocar
el resto del sistema (seed/demo u otros clientes).

NO borra los archivos de audio en disco -- nunca se copiaron, siguen donde
siempre estuvieron. Solo se borra el registro en la base. Para volver a
cargarlos, corre import_folder.py otra vez sobre la misma carpeta -- como
ya no van a existir como recording, se van a importar de nuevo (no hace
falta tocar nada del filtro de duplicados).

Por seguridad: sin --yes, SOLO MUESTRA cuantas filas se borrarian, no
borra nada. Hay que correrlo de nuevo con --yes para que borre de verdad.

Uso:
    .venv/bin/python scripts/delete_client_calls.py --client-name "Sin identificar (2M)"        # solo cuenta
    .venv/bin/python scripts/delete_client_calls.py --client-name "Sin identificar (2M)" --yes   # borra de verdad
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-name", required=True, help="name exacto del cliente/cartera a borrar")
    parser.add_argument("--yes", action="store_true", help="borrar de verdad (sin esto, solo cuenta)")
    args = parser.parse_args()

    with tenant_connection() as (conn, tenant_id):
        client_id = conn.execute(
            text("SELECT id FROM app.clients WHERE name = :n"), {"n": args.client_name}
        ).scalar_one_or_none()
        if client_id is None:
            print(f"No se encontro ningun cliente con name = {args.client_name!r}")
            raise SystemExit(1)

        n_calls = conn.execute(
            text("SELECT count(*) FROM app.calls WHERE client_id = :c"), {"c": client_id}
        ).scalar_one()
        n_recordings = conn.execute(
            text(
                "SELECT count(*) FROM app.recordings r JOIN app.calls c ON c.id = r.call_id "
                "WHERE c.client_id = :c"
            ),
            {"c": client_id},
        ).scalar_one()
        n_transcripts = conn.execute(
            text(
                "SELECT count(*) FROM app.transcripts t JOIN app.calls c ON c.id = t.call_id "
                "WHERE c.client_id = :c"
            ),
            {"c": client_id},
        ).scalar_one()
        n_campaign_analysis = conn.execute(
            text(
                "SELECT count(*) FROM app.call_campaign_analysis a JOIN app.calls c ON c.id = a.call_id "
                "WHERE c.client_id = :c"
            ),
            {"c": client_id},
        ).scalar_one()

        print(f"Cliente: {args.client_name} ({client_id})")
        print(f"  {n_calls} llamadas")
        print(f"  {n_recordings} grabaciones (registro en base -- los archivos en disco NO se tocan)")
        print(f"  {n_transcripts} transcripciones")
        print(f"  {n_campaign_analysis} analisis de campaña (IA)")

        if not args.yes:
            print("\nEsto fue solo un conteo -- no se borro nada. Corre de nuevo con --yes para borrar de verdad.")
            return

        print("\nBorrando...")
        conn.execute(
            text(
                "DELETE FROM app.call_campaign_analysis WHERE call_id IN "
                "(SELECT id FROM app.calls WHERE client_id = :c)"
            ),
            {"c": client_id},
        )
        conn.execute(
            text(
                "DELETE FROM app.transcript_segments WHERE transcript_id IN "
                "(SELECT t.id FROM app.transcripts t JOIN app.calls c ON c.id = t.call_id WHERE c.client_id = :c)"
            ),
            {"c": client_id},
        )
        conn.execute(
            text(
                "DELETE FROM app.transcripts WHERE call_id IN (SELECT id FROM app.calls WHERE client_id = :c)"
            ),
            {"c": client_id},
        )
        conn.execute(
            text(
                "DELETE FROM app.recordings WHERE call_id IN (SELECT id FROM app.calls WHERE client_id = :c)"
            ),
            {"c": client_id},
        )
        conn.execute(text("DELETE FROM app.calls WHERE client_id = :c"), {"c": client_id})

    print(f"Listo: {n_calls} llamadas y todo lo asociado borrado de la base para {args.client_name!r}.")
    print("Los archivos de audio en disco siguen ahi -- corre import_folder.py para volver a cargarlos.")


if __name__ == "__main__":
    main()
