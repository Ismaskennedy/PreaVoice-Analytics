"""Reporte 'Pulso Diario Operativo': datos REALES calculados desde lo que
ya tenemos guardado, no inventados.

Lo que si se puede calcular hoy: tiempo muerto (silencio largo dentro de
una llamada) a partir de los huecos entre segmentos de la transcripcion
(app.transcript_segments). Lo que NO se calcula todavia: deteccion de
buzon de voz, y el telefono real del deudor (no existe ese campo en
app.calls) -- por eso el reporte no incluye esas dos cosas como numeros,
solo las menciona como pendientes. Ver CLAUDE.md para el detalle."""
from collections import defaultdict
from datetime import date

from sqlalchemy import text

DEAD_TIME_THRESHOLD_MS = 60_000
MAX_EVENTS_PER_AGENT = 8

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def format_date_es(d: date) -> str:
    """Python's strftime usa el idioma del sistema operativo (aqui sale en
    ingles); esto evita depender de la configuracion regional de Windows."""
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} de {d.year}"


def build_pulso_diario(conn, report_date: date) -> dict:
    """conn ya debe tener tenant_session() activo (se llama desde una ruta
    que uso tenant_connection())."""
    total_calls = conn.execute(
        text(
            "SELECT count(*) FROM app.calls "
            "WHERE COALESCE(occurred_at, uploaded_at)::date = :report_date"
        ),
        {"report_date": report_date},
    ).scalar_one()

    segment_rows = conn.execute(
        text(
            "SELECT c.id AS call_id, c.agent_id, u.full_name AS agent_name, "
            "r.original_filename, ts.start_ms, ts.end_ms "
            "FROM app.calls c "
            "JOIN app.users u ON u.id = c.agent_id "
            "LEFT JOIN app.recordings r ON r.call_id = c.id "
            "JOIN app.transcripts t ON t.call_id = c.id AND t.state = 'CURRENT' "
            "JOIN app.transcript_segments ts ON ts.transcript_id = t.id "
            "WHERE COALESCE(c.occurred_at, c.uploaded_at)::date = :report_date "
            "ORDER BY c.id, ts.start_ms"
        ),
        {"report_date": report_date},
    ).mappings().all()

    # Agrupar los segmentos por llamada para poder ver los huecos entre uno y
    # el siguiente (un hueco grande = silencio = tiempo muerto real).
    segments_by_call = defaultdict(list)
    call_meta = {}
    for row in segment_rows:
        segments_by_call[row["call_id"]].append(row)
        call_meta[row["call_id"]] = {
            "call_id": row["call_id"],
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "filename": row["original_filename"] or "(sin archivo)",
        }

    events_by_agent = defaultdict(list)
    for call_id, segments in segments_by_call.items():
        meta = call_meta[call_id]
        for prev, nxt in zip(segments, segments[1:]):
            gap_ms = nxt["start_ms"] - prev["end_ms"]
            if gap_ms > DEAD_TIME_THRESHOLD_MS:
                events_by_agent[meta["agent_id"]].append(
                    {
                        "call_id": str(meta["call_id"]),
                        "filename": meta["filename"],
                        "gap_start_ms": prev["end_ms"],
                        "gap_end_ms": nxt["start_ms"],
                        "gap_ms": gap_ms,
                        "agent_name": meta["agent_name"],
                    }
                )

    agents = []
    for agent_id, events in events_by_agent.items():
        events.sort(key=lambda e: -e["gap_ms"])
        total_ms = sum(e["gap_ms"] for e in events)
        agents.append(
            {
                "agent_name": events[0]["agent_name"],
                "event_count": len(events),
                "total_minutes": round(total_ms / 60_000, 1),
                "critico": len(events) >= 5,
                "events": events[:MAX_EVENTS_PER_AGENT],
                "events_extra": max(0, len(events) - MAX_EVENTS_PER_AGENT),
            }
        )
    agents.sort(key=lambda a: -a["total_minutes"])
    for i, agent in enumerate(agents, start=1):
        agent["rank"] = i

    return {
        "report_date_label": format_date_es(report_date),
        "total_calls": total_calls,
        "total_events": sum(a["event_count"] for a in agents),
        "total_dead_minutes": round(sum(a["total_minutes"] for a in agents), 1),
        "agents": agents,
        "has_data": total_calls > 0,
    }
