"""Corre el analisis de campaña por IA (GPT-4o-mini) sobre las llamadas
que ya tienen transcripcion pero todavia no tienen este analisis -- capa
extra aparte del reporte por palabra clave (gratis) en
webapp/services/reports.py. Ver webapp/services/campaign_analysis.py para
el prompt exacto.

Costo por llamada es minimo (~$0.0003, GPT-4o-mini con la transcripcion ya
lista) -- no hace falta transcribir de nuevo, solo lee lo que ya esta en
app.transcript_segments.

Uso:
    .venv/bin/python -u scripts/analyze_campaigns.py
    .venv/bin/python -u scripts/analyze_campaigns.py --workers 15
    .venv/bin/python -u scripts/analyze_campaigns.py --limit 20   # prueba chica primero
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection
from webapp.openai_client import get_openai_client
from webapp.services.campaign_analysis import analyze_campaign

DEFAULT_WORKERS = 15
MAX_RETRIES = 3

_print_lock = Lock()


def _fetch_pending_calls(limit: int | None) -> list[dict]:
    with tenant_connection() as (conn, _tenant_id):
        query = (
            "SELECT t.call_id, string_agg(ts.text, ' ' ORDER BY ts.start_ms) AS transcript_text "
            "FROM app.transcripts t "
            "JOIN app.transcript_segments ts ON ts.transcript_id = t.id "
            "LEFT JOIN app.call_campaign_analysis a ON a.call_id = t.call_id AND a.state = 'CURRENT' "
            "WHERE t.state = 'CURRENT' AND a.id IS NULL "
            "GROUP BY t.call_id "
            "ORDER BY t.call_id"
        )
        if limit:
            query += " LIMIT :limit"
        rows = conn.execute(text(query), {"limit": limit} if limit else {}).mappings().all()
    return [dict(r) for r in rows]


def _save_analysis(call_id: str, result: dict) -> None:
    with tenant_connection() as (conn, tenant_id):
        conn.execute(
            text(
                "UPDATE app.call_campaign_analysis SET state = 'SUPERSEDED' "
                "WHERE call_id = :call_id AND state = 'CURRENT'"
            ),
            {"call_id": call_id},
        )
        conn.execute(
            text(
                "INSERT INTO app.call_campaign_analysis "
                "(tenant_id, call_id, model, campana, confianza, evidencia, "
                "telefono_mencionado, nombre_gestor, nombre_cliente, raw_response) "
                "VALUES (:tenant_id, :call_id, :model, :campana, :confianza, :evidencia, "
                ":telefono_mencionado, :nombre_gestor, :nombre_cliente, :raw_response)"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "model": "gpt-4o-mini", **result},
        )


def _mark_failed(call_id: str, error: str) -> None:
    with _print_lock:
        print(f"  [FALLO] {call_id}: {error}")


def _process_one(openai_client, call: dict) -> bool:
    call_id = call["call_id"]
    transcript_text = call["transcript_text"] or ""
    if not transcript_text.strip():
        _mark_failed(call_id, "transcripcion vacia")
        return False

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = analyze_campaign(openai_client, transcript_text)
            _save_analysis(str(call_id), result)
            return True
        except Exception as exc:  # noqa: BLE001 - una llamada mala no debe tumbar el lote
            last_error = exc
            time.sleep(2 ** attempt)

    _mark_failed(call_id, str(last_error))
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="llamadas a GPT-4o-mini en paralelo")
    parser.add_argument("--limit", type=int, default=None, help="procesar solo las primeras N (para probar)")
    args = parser.parse_args()

    calls = _fetch_pending_calls(args.limit)
    total = len(calls)
    if total == 0:
        print("No hay llamadas transcritas pendientes de analizar.")
        return

    print(f"Analizando campaña de {total} llamadas ({args.workers} en paralelo)...")
    openai_client = get_openai_client()

    done = 0
    ok = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_process_one, openai_client, call): call for call in calls}
        for future in as_completed(futures):
            done += 1
            if future.result():
                ok += 1
            if done % 50 == 0 or done == total:
                elapsed = time.time() - start
                with _print_lock:
                    print(f"  {done}/{total} ({ok} ok) -- {elapsed:.0f}s transcurridos")

    elapsed = time.time() - start
    print(f"Listo: {ok}/{total} analizadas en {elapsed:.0f}s. {total - ok} fallaron.")


if __name__ == "__main__":
    main()
