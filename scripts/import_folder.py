"""Importa grabaciones directo de una carpeta en disco (recursivo, cualquier
estructura de subcarpetas) -- pensado para lotes enormes (cientos de miles
a millones de archivos, como el disco completo).

A esta escala NO copia los archivos a otro lado -- ya viven en el disco
montado, y copiarlos duplicaria cientos de GB y tardaria muchisimo. En vez
de eso, cada llamada queda apuntando directo a donde ya esta el archivo
(storage_path = la ruta real dentro de la carpeta que le diste). Cada
archivo se lee una sola vez, para calcular su checksum.

Mismo gestor y mismo cliente para todo el lote, igual que import_zip.py.
Telefono/fecha se intentan sacar del nombre del archivo (si no hace match,
quedan en blanco).

No transcribe ni analiza -- eso es scripts/transcribe_batch.py aparte, y a
este volumen conviene decidir con calma cuantos segundos/cuantas llamadas
antes de lanzarlo (son millones de llamadas, no miles -- revisa el costo
real primero).

Uso:
    .venv/bin/python scripts/import_folder.py /mnt/recordings/TODAS \
        --agent-name "Recuperado (SystemRescue)" \
        --client-name "Sin identificar (recuperadas)"
    .venv/bin/python scripts/import_folder.py /mnt/recordings/TODAS --limit 50   # probar primero

Para un lote que va a tardar horas, corre esto dentro de screen/tmux o con
nohup, para que no se corte si se cae la sesion de SSH. Ejemplo:
    nohup .venv/bin/python -u scripts/import_folder.py /mnt/recordings/TODAS \
        --agent-name "..." --client-name "..." > import.log 2>&1 &
"""
import argparse
import hashlib
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection
from webapp.main import ALLOWED_AUDIO_EXT, _normalize_phone, _parse_occurred_at

CHUNK_SIZE = 1000
DEFAULT_WORKERS = 8


def _discover_files(root: Path, limit: int | None):
    found = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in ALLOWED_AUDIO_EXT:
            yield path
            found += 1
            if limit and found >= limit:
                return


def _read_one(source: Path):
    try:
        return source, source.read_bytes(), None
    except Exception as exc:  # noqa: BLE001 - un archivo malo no debe tumbar el resto del lote
        return source, None, str(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="carpeta con las grabaciones (se recorre completa, con subcarpetas)")
    parser.add_argument("--agent-name", required=True, help="full_name exacto del gestor (todo el lote)")
    parser.add_argument("--client-name", required=True, help="name exacto del cliente/cartera (todo el lote)")
    parser.add_argument("--limit", type=int, default=None, help="importar solo las primeras N (para probar)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="lecturas de archivo en paralelo")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"No existe la carpeta: {root}")
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

    print("Buscando archivos de audio (puede tardar varios minutos en un disco de millones)...")
    files = list(_discover_files(root, args.limit))
    total = len(files)
    print(
        f"{total} archivos encontrados. Importando "
        f"(gestor={args.agent_name}, cliente={args.client_name}, {args.workers} lecturas en paralelo)..."
    )

    loaded = 0
    failed: list[str] = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk = files[chunk_start : chunk_start + CHUNK_SIZE]
            call_rows = []
            recording_rows = []

            for source, data, error in pool.map(_read_one, chunk):
                if error is not None:
                    failed.append(f"{source.name}: {error}")
                    continue
                call_id = str(uuid.uuid4())
                filename = source.name
                call_rows.append(
                    {
                        "id": call_id,
                        "client_id": client_id,
                        "agent_id": agent_id,
                        "uploaded_by": agent_id,
                        "occurred_at": _parse_occurred_at(filename, ""),
                        "phone_number": _normalize_phone(None, filename),
                    }
                )
                recording_rows.append(
                    {
                        "call_id": call_id,
                        "storage_path": str(source.resolve()),
                        "original_filename": filename,
                        "checksum": hashlib.sha256(data).hexdigest(),
                        "format": source.suffix.lstrip("."),
                    }
                )

            if call_rows:
                with tenant_connection() as (conn, tenant_id):
                    for row in call_rows:
                        row["tenant_id"] = tenant_id
                    conn.execute(
                        text(
                            "INSERT INTO app.calls "
                            "(id, tenant_id, client_id, agent_id, uploaded_by, occurred_at, phone_number) "
                            "VALUES (:id, :tenant_id, :client_id, :agent_id, :uploaded_by, :occurred_at, :phone_number)"
                        ),
                        call_rows,
                    )
                    for row in recording_rows:
                        row["tenant_id"] = tenant_id
                    conn.execute(
                        text(
                            "INSERT INTO app.recordings "
                            "(tenant_id, call_id, storage_path, original_filename, checksum_sha256, format) "
                            "VALUES (:tenant_id, :call_id, :storage_path, :original_filename, :checksum, :format)"
                        ),
                        recording_rows,
                    )
                loaded += len(call_rows)

            done = min(chunk_start + CHUNK_SIZE, total)
            elapsed = time.time() - start
            rate = loaded / elapsed if elapsed > 0 else 0
            print(f"  {done}/{total} ({loaded} cargados) -- {elapsed:.0f}s transcurridos, ~{rate:.1f}/s")

    print(f"\nListo: {loaded}/{total} cargadas. {len(failed)} fallaron.")
    if failed:
        print("Primeros errores:")
        for line in failed[:10]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
