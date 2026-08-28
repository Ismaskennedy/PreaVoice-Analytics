"""Reporte 'Pulso Diario Operativo': datos REALES calculados desde lo que
ya tenemos guardado, no inventados.

Lo que si se puede calcular hoy: tiempo muerto (silencio largo dentro de
una llamada) a partir de los huecos entre segmentos de la transcripcion
(app.transcript_segments). Lo que NO se calcula todavia: deteccion de
buzon de voz, y el telefono real del deudor (no existe ese campo en
app.calls) -- por eso el reporte no incluye esas dos cosas como numeros,
solo las menciona como pendientes. Ver CLAUDE.md para el detalle."""
import re
import unicodedata
from collections import defaultdict
from datetime import date

from sqlalchemy import text

_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

# Reporte de campañas: a que cartera pertenece cada llamada segun lo que se
# MENCIONA en la transcripcion, no segun el cliente al que se subio (util
# para medir campañas y detectar cruces). Deteccion por palabra clave, sin
# IA -- no hace falta gastar credito de OpenAI en esto, solo transcripcion.
CAMPAIGNS = {
    "SPIN": ("spin", "oxxo"),
    "BRADESCARD": ("bradescard",),
    "BANCO AZTECA": ("banco azteca", "azteca"),
    # "despegar" y "walmart" son palabras de uso comun (el verbo "despegar",
    # la tienda que cualquiera menciona sin relacion al banco) -- mas
    # riesgo de falso positivo que el resto de las palabras clave de aqui.
    "INVEX": (
        "invex", "volaris", "manchester", "sicard", "cibanco",
        "credomatic", "despegar", "walmart", "voyage",
    ),
}

# Fechas dichas en la llamada ("15 de agosto", "20 de agosto de 2026") --
# util para grabaciones recuperadas sin fecha en el archivo. Es un patron de
# texto, no IA: aproximado a proposito ("mas o menos de que fecha son"), no
# entiende fechas relativas ("mañana", "el lunes") porque no hay una fecha de
# referencia confiable para esas llamadas.
_DATE_PATTERN = re.compile(
    rf"\b(\d{{1,2}})\s+de\s+({'|'.join(_MESES)})(?:\s+de[l]?\s+(\d{{4}}))?\b"
)


def _normalize(text_value: str) -> str:
    stripped = unicodedata.normalize("NFKD", text_value).encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


def _find_dates(normalized_text: str) -> list[str]:
    labels = []
    for day, month, year in _DATE_PATTERN.findall(normalized_text):
        label = f"{int(day)} de {month}" + (f" de {year}" if year else "")
        if label not in labels:
            labels.append(label)
    return labels


# Nombre del gestor, si se presenta durante la llamada ("mi nombre es...",
# "le habla..."). Igual que las fechas: patron de texto aproximado, no IA --
# "mas o menos quien es", no una identificacion confiable. Se corta la
# captura en la primera palabra que no parece parte de un nombre (una
# palabra de esta lista, o la mencion de una campaña) para no arrastrar el
# resto de la frase ("...para informarle", "...de parte de Spin").
_NAME_TRIGGER = re.compile(
    r"\b(?:mi nombre es|me llamo|mi nombre|habla con usted|le habla)\s+([a-z]+(?:\s+[a-z]+){0,3})"
)
_NAME_STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "para", "con", "que", "sobre",
    "respecto", "su", "cuenta", "empresa", "parte", "representando",
    "representacion", "nombre", "llamando", "hablando", "y", "en", "por",
    "spin", "bradescard", "azteca", "banco",
}


def _find_agent_names(normalized_text: str) -> list[str]:
    names = []
    for match in _NAME_TRIGGER.finditer(normalized_text):
        kept = []
        for word in match.group(1).split():
            if word in _NAME_STOPWORDS:
                break
            kept.append(word)
        if kept:
            label = " ".join(kept).title()
            if label not in names:
                names.append(label)
    return names


# Llamadas "vacias": buzon de voz, tono ocupado, no contesto -- sin
# contenido real de conversacion, asi que nunca van a mencionar una
# campaña. Se separan de "sin detectar" (que si tuvo conversacion real,
# solo que no menciono ninguna campaña) para no mezclar ambos casos.
_EMPTY_PHRASES = (
    "buzon de voz", "grabe su mensaje", "deje su mensaje", "marque la tecla",
    "no disponible", "fuera de servicio", "apagado o fuera del area",
    "el numero que usted marco", "intente mas tarde", "despues del tono",
)
_MIN_WORDS_FOR_REAL_CALL = 8


def _is_empty_call(normalized_text: str) -> bool:
    if len(normalized_text.split()) < _MIN_WORDS_FOR_REAL_CALL:
        return True
    return any(phrase in normalized_text for phrase in _EMPTY_PHRASES)


def build_campaign_report(conn) -> dict:
    """conn ya debe tener tenant_session() activo. Solo mira llamadas que ya
    tienen transcripcion -- las que no, se cuentan aparte para que quede
    claro que falta transcribirlas antes de aparecer aqui."""
    total_calls = conn.execute(text("SELECT count(*) FROM app.calls")).scalar_one()

    rows = conn.execute(
        text(
            """
            SELECT c.id AS call_id, c.occurred_at, c.uploaded_at, c.phone_number,
                   cl.name AS client_name, u.full_name AS agent_name,
                   rec.storage_path, rec.original_filename,
                   string_agg(ts.text, ' ' ORDER BY ts.start_ms) AS full_text
            FROM app.calls c
            JOIN app.clients cl ON cl.id = c.client_id
            JOIN app.users u ON u.id = c.agent_id
            LEFT JOIN app.recordings rec ON rec.call_id = c.id
            JOIN app.transcripts t ON t.call_id = c.id AND t.state = 'CURRENT'
            JOIN app.transcript_segments ts ON ts.transcript_id = t.id
            GROUP BY c.id, c.occurred_at, c.uploaded_at, c.phone_number, cl.name, u.full_name,
                     rec.storage_path, rec.original_filename
            ORDER BY COALESCE(c.occurred_at, c.uploaded_at) DESC
            """
        )
    ).mappings().all()

    counts = {name: 0 for name in CAMPAIGNS}
    counts["sin detectar"] = 0
    counts["varias"] = 0
    counts["vacía"] = 0

    calls = []
    for row in rows:
        normalized = _normalize(row["full_text"] or "")
        detected = [
            name
            for name, keywords in CAMPAIGNS.items()
            if any(re.search(rf"\b{re.escape(kw)}\b", normalized) for kw in keywords)
        ]
        if len(detected) == 1:
            bucket = detected[0]
        elif len(detected) > 1:
            bucket = "varias"
        elif _is_empty_call(normalized):
            bucket = "vacía"
        else:
            bucket = "sin detectar"
        counts[bucket] += 1

        assigned = (row["client_name"] or "").strip().upper()
        calls.append(
            {
                "call_id": str(row["call_id"]),
                "when": row["occurred_at"] or row["uploaded_at"],
                "phone_number": row["phone_number"],
                "original_filename": row["original_filename"],
                "agent_name": row["agent_name"],
                "assigned_client": row["client_name"],
                "detected": detected,
                "detected_label": "llamada vacía" if bucket == "vacía" else (" + ".join(detected) if detected else "sin detectar"),
                "bucket": bucket,
                "mismatch": bool(detected) and assigned not in [d.upper() for d in detected],
                "dates_mentioned": _find_dates(normalized),
                "agent_names_mentioned": _find_agent_names(normalized),
                "storage_path": row["storage_path"],
            }
        )

    mentioned_agent_names = sorted({name for call in calls for name in call["agent_names_mentioned"]})

    return {
        "calls": calls,
        "counts": counts,
        "campaign_names": list(CAMPAIGNS.keys()),
        "mentioned_agent_names": mentioned_agent_names,
        "total_with_transcript": len(rows),
        "total_without_transcript": total_calls - len(rows),
        "total_calls": total_calls,
    }

DEAD_TIME_THRESHOLD_MS = 60_000
MAX_EVENTS_PER_AGENT = 8

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


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
