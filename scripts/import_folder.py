"""Importa grabaciones directo de una carpeta en disco (recursivo, cualquier
estructura de subcarpetas) -- pensado para lotes enormes (cientos de miles
a millones de archivos, como el disco completo).

A esta escala NO copia los archivos a otro lado -- ya viven en el disco
montado, y copiarlos duplicaria cientos de GB y tardaria muchisimo. En vez
de eso, cada llamada queda apuntando directo a donde ya esta el archivo
(storage_path = la ruta real dentro de la carpeta que le diste).

Se salta (no se sube) cualquier archivo mas corto que --min-seconds (40s
por default): esas grabaciones casi siempre son "no contesto"/timbrado sin
respuesta, sin conversacion real que valga la pena transcribir despues.
Cada archivo que si pasa el filtro se lee una sola vez, para calcular su
checksum -- la duracion se saca con ffprobe (rapido, no decodifica el
audio completo).

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
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection
from webapp.main import ALLOWED_AUDIO_EXT, _normalize_phone, _parse_occurred_at

CHUNK_SIZE = 1000
DEFAULT_WORKERS = 8
DEFAULT_MIN_SECONDS = 40

# Ruta conocida de la instalacion portable de ffmpeg/ffprobe en Windows; en
# Linux (con ffmpeg instalado por apt) shutil.which("ffprobe") ya lo
# encuentra solo.
_KNOWN_FFPROBE = Path.home() / "ffmpeg" / "ffmpeg-9.0.1-essentials_build" / "bin" / "ffprobe.exe"
FFPROBE_PATH = str(_KNOWN_FFPROBE) if _KNOWN_FFPROBE.exists() else (shutil.which("ffprobe") or "ffprobe")


def _discover_files(root: Path, limit: int | None):
    found = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in ALLOWED_AUDIO_EXT:
            yield path
            found += 1
            if limit and found >= limit:
                return


def _get_duration_seconds(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:  # noqa: BLE001 - si no se puede medir, mejor no descartarla de entrada
        return None


def _read_one(source: Path, min_seconds: float):
    """Regresa (source, data, error, too_short). Se salta el archivo (data
    y error en None, too_short=True) si dura menos de min_seconds."""
    duration = _get_duration_seconds(source)
    if duration is not None and duration < min_seconds:
        return source, None, None, True
    try:
        return source, source.read_bytes(), None, False
    except Exception as exc:  # noqa: BLE001 - un archivo malo no debe tumbar el resto del lote
        return source, None, str(exc), False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="carpeta con las grabaciones (se recorre completa, con subcarpetas)")
    parser.add_argument("--agent-name", required=True, help="full_name exacto del gestor (todo el lote)")
    parser.add_argument("--client-name", required=True, help="name exacto del cliente/cartera (todo el lote)")
    parser.add_argument("--limit", type=int, default=None, help="importar solo las primeras N (para probar)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="lecturas de archivo en paralelo")
    parser.add_argument(
        "--min-seconds", type=float, default=DEFAULT_MIN_SECONDS,
        help="se saltan (no se suben) los archivos mas cortos que esto",
    )
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"No existe la carpeta: {root}")
        raise SystemExit(1)

    if FFPROBE_PATH == "ffprobe" and shutil.which("ffprobe") is None:
        print("No se encontro ffprobe (viene con ffmpeg). Instalalo para poder filtrar por duracion.")
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
        f"{total} archivos encontrados. Importando (se saltan los menores a {args.min_seconds}s) "
        f"(gestor={args.agent_name}, cliente={args.client_name}, {args.workers} en paralelo)..."
    )

    loaded = 0
    too_short = 0
    failed: list[str] = []
    start = time.time()
    read_one = partial(_read_one, min_seconds=args.min_seconds)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk = files[chunk_start : chunk_start + CHUNK_SIZE]
            call_rows = []
            recording_rows = []

            for source, data, error, is_short in pool.map(read_one, chunk):
                if is_short:
                    too_short += 1
                    continue
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
            rate = (loaded + too_short) / elapsed if elapsed > 0 else 0
            print(
                f"  {done}/{total} ({loaded} cargados, {too_short} muy cortos, omitidos) "
                f"-- {elapsed:.0f}s transcurridos, ~{rate:.1f}/s"
            )

    print(f"\nListo: {loaded}/{total} cargadas. {too_short} omitidas por muy cortas. {len(failed)} fallaron.")
    if failed:
        print("Primeros errores:")
        for line in failed[:10]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
