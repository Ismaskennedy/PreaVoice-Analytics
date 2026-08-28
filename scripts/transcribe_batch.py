"""Transcribe en lote las llamadas que todavia no tienen transcripcion, en
dos pasos para no gastar de mas en buzones de voz / llamadas vacias:

1. Sondeo corto (PROBE_SECONDS, 20s por default) al inicio de la llamada.
2. Si el sondeo se parece a buzon de voz o no tiene suficiente conversacion
   real (misma logica exacta que ya usa el reporte de campañas para marcar
   "llamada vacia" -- webapp/services/reports.py), se deja ahi: esa
   transcripcion corta ya es suficiente, no tiene caso pagar por mas.
3. Si el sondeo SI parece conversacion real, se transcribe hasta
   MAX_SECONDS (3.5 min por default) -- mas que el sondeo para no perder
   una mencion tardia de palabra clave, pero con tope para no pagar
   llamadas larguisimas completas.

No borra ni excluye las llamadas vacias del sistema (siguen apareciendo en
el reporte, marcadas como tal) -- solo evita pagarles una transcripcion
larga que no va a aportar nada.

No corre ningun analisis con GPT-4o-mini (checklist/emociones/coaching) --
solo transcripcion.

Uso:
    .venv/Scripts/python.exe scripts/transcribe_batch.py
    .venv/Scripts/python.exe scripts/transcribe_batch.py --probe-seconds 15 --max-seconds 180 --workers 25
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
from webapp.services.reports import _is_empty_call, _normalize
from webapp.services.transcription import transcribe

DEFAULT_PROBE_SECONDS = 20
DEFAULT_MAX_SECONDS = 210  # 3.5 minutos
DEFAULT_WORKERS = 20
MAX_RETRIES = 3

# Ruta conocida de la instalacion portable de ffmpeg en Windows; en Linux
# (con ffmpeg instalado por apt) shutil.which("ffmpeg") ya lo encuentra solo.
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


def _transcribe_with_retries(openai_client, path: Path):
    """Intenta transcribir hasta MAX_RETRIES veces. Regresa (segments, None)
    o (None, error) -- nunca lanza, para que el llamador decida que hacer."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            segments, _raw = transcribe(openai_client, path)
            return segments, None
        except Exception as exc:  # noqa: BLE001 - un intento fallido no debe tumbar el lote
            last_error = exc
            time.sleep(2 ** attempt)
    return None, last_error


def _process_one(openai_client, call: dict, probe_seconds: int, max_seconds: int) -> bool:
    call_id = call["call_id"]
    source_path = Path(call["storage_path"])
    if not source_path.exists():
        _mark_failed(call_id, f"no se encontro el archivo {source_path}")
        return False

    with tempfile.TemporaryDirectory() as tmp_dir:
        probe_path = Path(tmp_dir) / f"probe{source_path.suffix}"
        try:
            _trim_audio(source_path, probe_seconds, probe_path)
        except subprocess.CalledProcessError as exc:
            _mark_failed(call_id, f"ffmpeg fallo: {exc.stderr[:200] if exc.stderr else exc}")
            return False

        probe_segments, error = _transcribe_with_retries(openai_client, probe_path)
        if probe_segments is None:
            _mark_failed(call_id, str(error))
            return False

        probe_text = " ".join(seg["text"] for seg in probe_segments)
        if _is_empty_call(_normalize(probe_text)):
            # Buzon de voz / sin conversacion real: el sondeo ya alcanza.
            # No se borra ni se excluye -- sigue viva, solo no se paga mas.
            _save_transcript(str(call_id), probe_segments)
            return True

        # Conversacion real: se transcribe hasta max_seconds -- mas que el
        # sondeo para no perder una mencion tardia, pero con tope para no
        # pagar llamadas larguisimas completas.
        capped_path = Path(tmp_dir) / f"capped{source_path.suffix}"
        try:
            _trim_audio(source_path, max_seconds, capped_path)
        except subprocess.CalledProcessError as exc:
            _mark_failed(call_id, f"ffmpeg fallo (tope): {exc.stderr[:200] if exc.stderr else exc}")
            return False

        full_segments, error = _transcribe_with_retries(openai_client, capped_path)

    if full_segments is None:
        _mark_failed(call_id, str(error))
        return False

    _save_transcript(str(call_id), full_segments)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-seconds", type=int, default=DEFAULT_PROBE_SECONDS,
        help="segundos del sondeo inicial para detectar buzon de voz",
    )
    parser.add_argument(
        "--max-seconds", type=int, default=DEFAULT_MAX_SECONDS,
        help="tope de segundos a transcribir cuando la llamada SI parece real (210 = 3.5 min)",
    )
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

    print(
        f"Transcribiendo {total} llamadas (sondeo de {args.probe_seconds}s, hasta {args.max_seconds}s si no es "
        f"vacia, {args.workers} en paralelo)..."
    )
    openai_client = get_openai_client()

    done = 0
    ok = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_process_one, openai_client, call, args.probe_seconds, args.max_seconds): call
            for call in calls
        }
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
