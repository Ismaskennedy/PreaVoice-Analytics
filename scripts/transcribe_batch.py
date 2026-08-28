"""Transcribe en lote las llamadas que todavia no tienen transcripcion,
recortando cada audio a los primeros N segundos antes de mandarlo a Whisper.

Pensado para el reporte de campañas (webapp/services/reports.py): ese
reporte solo necesita saber si al inicio de la llamada se menciona SPIN,
Bradescard o Banco Azteca -- no hace falta transcribir la llamada completa
para eso. Recortar el audio antes de subirlo a Whisper baja el costo y el
tiempo de forma proporcional (Whisper cobra por minuto de audio que le
mandas, no por llamada).

No corre ningun analisis con GPT-4o-mini (checklist/emociones/coaching) --
solo transcripcion. Las llamadas quedan en status 'transcribed', listas
para el reporte de campañas; si mas adelante se quieren analizar de verdad,
eso es un paso aparte (necesitarian la llamada completa, no el recorte).

Uso:
    .venv/Scripts/python.exe scripts/transcribe_batch.py
    .venv/Scripts/python.exe scripts/transcribe_batch.py --seconds 20 --workers 25
    .venv/Scripts/python.exe scripts/transcribe_batch.py --limit 20   # prueba chica primero
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection
from webapp.openai_client import get_openai_client
from webapp.services.transcription import transcribe

DEFAULT_SECONDS = 30
DEFAULT_WORKERS = 20
MAX_RETRIES = 3

# Ruta conocida de la instalacion portable de ffmpeg en esta maquina; si no
# existe, se usa "ffmpeg" del PATH (por si algun dia se instala de verdad).
_KNOWN_FFMPEG = Path.home() / "ffmpeg" / "ffmpeg-9.0.1-essentials_build" / "bin" / "ffmpeg.exe"
FFMPEG_PATH = str(_KNOWN_FFMPEG) if _KNOWN_FFMPEG.exists() else (shutil.which("ffmpeg") or "ffmpeg")

_print_lock = Lock()


def _fetch_pending_calls(limit: int | None) -> list[dict]:
    with tenant_connection() as (conn, _tenant_id):
        query = (
            "SELECT c.id AS call_id, r.storage_path "
            "FROM app.calls c "
            "JOIN app.recordings r ON r.call_id = c.id "
            "LEFT JOIN app.transcripts t ON t.call_id = c.id AND t.state = 'CURRENT' "
            "WHERE t.id IS NULL AND r.storage_path IS NOT NULL "
            "ORDER BY c.uploaded_at"
        )
        if limit:
            query += " LIMIT :limit"
        rows = conn.execute(text(query), {"limit": limit} if limit else {}).mappings().all()
    return [dict(r) for r in rows]


def _trim_audio(source_path: Path, seconds: int, dest_path: Path) -> None:
    subprocess.run(
        [FFMPEG_PATH, "-y", "-i", str(source_path), "-t", str(seconds), "-c", "copy", str(dest_path)],
        check=True,
        capture_output=True,
    )


def _save_transcript(call_id: str, segments: list[dict]) -> None:
    with tenant_connection() as (conn, tenant_id):
        transcript_id = conn.execute(
            text(
                "INSERT INTO app.transcripts (tenant_id, call_id, provider) "
                "VALUES (:tenant_id, :call_id, 'openai-whisper') RETURNING id"
            ),
            {"tenant_id": tenant_id, "call_id": call_id},
        ).scalar_one()
        for segment in segments:
            conn.execute(
                text(
                    "INSERT INTO app.transcript_segments "
                    "(tenant_id, transcript_id, speaker, start_ms, end_ms, text) "
                    "VALUES (:tenant_id, :transcript_id, :speaker, :start_ms, :end_ms, :text)"
                ),
                {"tenant_id": tenant_id, "transcript_id": transcript_id, **segment},
            )
        conn.execute(
            text("UPDATE app.calls SET status = 'transcribed' WHERE id = :call_id"), {"call_id": call_id}
        )


def _mark_failed(call_id: str, error: str) -> None:
    with tenant_connection() as (conn, _tenant_id):
        conn.execute(text("UPDATE app.calls SET status = 'failed' WHERE id = :call_id"), {"call_id": call_id})
    with _print_lock:
        print(f"  [FALLO] {call_id}: {error}")


def _process_one(openai_client, call: dict, seconds: int) -> bool:
    call_id = call["call_id"]
    source_path = Path(call["storage_path"])
    if not source_path.exists():
        _mark_failed(call_id, f"no se encontro el archivo {source_path}")
        return False

    with tempfile.TemporaryDirectory() as tmp_dir:
        trimmed_path = Path(tmp_dir) / f"clip{source_path.suffix}"
        try:
            _trim_audio(source_path, seconds, trimmed_path)
        except subprocess.CalledProcessError as exc:
            _mark_failed(call_id, f"ffmpeg fallo: {exc.stderr[:200] if exc.stderr else exc}")
            return False

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                segments, _raw = transcribe(openai_client, trimmed_path)
                _save_transcript(str(call_id), segments)
                return True
            except Exception as exc:  # noqa: BLE001 - una llamada mala no debe tumbar el lote
                last_error = exc
                time.sleep(2 ** attempt)

        _mark_failed(call_id, str(last_error))
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=DEFAULT_SECONDS, help="segundos a transcribir desde el inicio")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="llamadas a Whisper en paralelo")
    parser.add_argument("--limit", type=int, default=None, help="procesar solo las primeras N (para probar)")
    args = parser.parse_args()

    if FFMPEG_PATH == "ffmpeg" and shutil.which("ffmpeg") is None:
        print("No se encontro ffmpeg. Instalalo o ajusta FFMPEG_PATH en este script.")
        raise SystemExit(1)

    calls = _fetch_pending_calls(args.limit)
    total = len(calls)
    if total == 0:
        print("No hay llamadas pendientes de transcribir.")
        return

    print(f"Transcribiendo {total} llamadas (primeros {args.seconds}s cada una, {args.workers} en paralelo)...")
    openai_client = get_openai_client()

    done = 0
    ok = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_process_one, openai_client, call, args.seconds): call for call in calls}
        for future in as_completed(futures):
            done += 1
            if future.result():
                ok += 1
            if done % 25 == 0 or done == total:
                elapsed = time.time() - start
                with _print_lock:
                    print(f"  {done}/{total} ({ok} ok) -- {elapsed:.0f}s transcurridos")

    elapsed = time.time() - start
    print(f"Listo: {ok}/{total} transcritas en {elapsed:.0f}s. {total - ok} fallaron (status='failed').")


if __name__ == "__main__":
    main()
