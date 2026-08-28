"""Importa un ZIP de grabaciones directo desde disco -- sin pasar por el
navegador/HTTP. Pensado para lotes grandes (cientos de MB o varios GB):
subir un ZIP asi por la pantalla web significaria cargarlo completo en
memoria del servidor, arriesgado a ese tamaño. Este script lee el ZIP
miembro por miembro (streaming, no todo en RAM de un jalon) y usa la misma
logica que POST /calls/upload-zip: mismo gestor y mismo cliente para todo
el lote, telefono/fecha se intentan sacar del nombre del archivo (si no
hace match, quedan en blanco -- normal en grabaciones recuperadas por
forense, que no traen nombre original).

No transcribe ni analiza -- eso es scripts/transcribe_batch.py aparte.

Uso:
    .venv/Scripts/python.exe scripts/import_zip.py "D:\\bloque1.zip" \\
        --agent-name "Recuperado (SystemRescue)" \\
        --client-name "Sin identificar (recuperadas)"
"""
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.config import sharded_recording_path
from webapp.db import tenant_connection
from webapp.main import ALLOWED_AUDIO_EXT, _normalize_phone, _parse_occurred_at


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", help="ruta al .zip en disco")
    parser.add_argument("--agent-name", required=True, help="full_name exacto del gestor (todo el lote)")
    parser.add_argument("--client-name", required=True, help="name exacto del cliente/cartera (todo el lote)")
    parser.add_argument("--limit", type=int, default=None, help="importar solo las primeras N (para probar)")
    args = parser.parse_args()

    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        print(f"No existe el archivo: {zip_path}")
        raise SystemExit(1)

    with tenant_connection() as (conn, _tenant_id):
        agent_id = conn.execute(
            text("SELECT id FROM app.users WHERE full_name = :n"), {"n": args.agent_name}
        ).scalar_one_or_none()
        client_id = conn.execute(
            text("SELECT id FROM app.clients WHERE name = :n"), {"n": args.client_name}
        ).scalar_one_or_none()

    if agent_id is None:
        print(f"No se encontro ningun gestor con full_name = {args.agent_name!r}")
        raise SystemExit(1)
    if client_id is None:
        print(f"No se encontro ningun cliente con name = {args.client_name!r}")
        raise SystemExit(1)

    loaded = 0
    skipped_not_audio = 0
    failed: list[str] = []

    with zipfile.ZipFile(zip_path) as archive:
        entries = [e for e in archive.infolist() if not e.is_dir()]
        if args.limit:
            entries = entries[: args.limit]
        total = len(entries)
        print(f"{total} archivos en el ZIP. Importando (gestor={args.agent_name}, cliente={args.client_name})...")

        for i, entry in enumerate(entries, start=1):
            filename = Path(entry.filename).name
            extension = Path(filename).suffix.lower()
            if extension not in ALLOWED_AUDIO_EXT:
                skipped_not_audio += 1
                continue

            try:
                audio_bytes = archive.read(entry)
                with tenant_connection() as (conn, tenant_id):
                    call_id = conn.execute(
                        text(
                            "INSERT INTO app.calls "
                            "(tenant_id, client_id, agent_id, uploaded_by, occurred_at, phone_number) "
                            "VALUES (:tenant_id, :client_id, :agent_id, :uploaded_by, :occurred_at, :phone_number) "
                            "RETURNING id"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "client_id": client_id,
                            "agent_id": agent_id,
                            "uploaded_by": agent_id,  # sin usuario admin en un script de linea de comandos
                            "occurred_at": _parse_occurred_at(filename, ""),
                            "phone_number": _normalize_phone(None, filename),
                        },
                    ).scalar_one()

                    storage_path = sharded_recording_path(call_id, extension)
                    storage_path.write_bytes(audio_bytes)

                    conn.execute(
                        text(
                            "INSERT INTO app.recordings "
                            "(tenant_id, call_id, storage_path, original_filename, checksum_sha256, format) "
                            "VALUES (:tenant_id, :call_id, :storage_path, :original_filename, :checksum, :format)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "call_id": call_id,
                            "storage_path": str(storage_path),
                            "original_filename": filename,
                            "checksum": hashlib.sha256(audio_bytes).hexdigest(),
                            "format": extension.lstrip("."),
                        },
                    )
                loaded += 1
            except Exception as exc:  # noqa: BLE001 - un archivo malo no debe tumbar el resto del lote
                failed.append(f"{filename}: {exc}")

            if i % 500 == 0 or i == total:
                print(f"  {i}/{total} ({loaded} cargados, {len(failed)} fallidos)")

    print(f"\nListo: {loaded} cargados, {skipped_not_audio} no eran audio, {len(failed)} fallaron.")
    if failed:
        print("Primeros errores:")
        for line in failed[:10]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
