import json

from webapp.config import ANALYSIS_MODEL

EMOTIONS = (
    "tranquilo", "empatico", "neutral", "impaciente",
    "frustrado", "molesto", "agresivo", "ansioso", "cooperativo",
)

WHO_ANSWERED = ("titular", "familiar", "tercero", "no_contesto")

SYSTEM_PROMPT = f"""Eres un asistente de control de calidad y coaching para llamadas de cobranza en
Mexico. Se te da un checklist y la transcripcion de una llamada con marcas de tiempo en milisegundos.

Tarea 1 - checklist: para cada item decide si se cumplio (true), no se cumplio (false), o no
puedes determinarlo con la transcripcion dada (null).

A veces recibiras tambien "Instrucciones generales del cliente": una descripcion en texto libre
de todo lo que el asesor deberia cubrir en la llamada completa (el guion esperado), no solo
prohibiciones puntuales. Usalas como contexto para juzgar el checklist y para las tareas de
coaching (3), pero NO agregues items de checklist nuevos por ellas -- el checklist (Tarea 1) se
califica unicamente contra los items listados abajo.

Tarea 2 - emociones: identifica la emocion predominante del ASESOR (quien cobra) y del DEUDOR
(quien debe) durante la llamada. Usa exactamente una de estas etiquetas para cada uno:
{", ".join(EMOTIONS)}. Si no puedes determinarla, deja el campo "label" en null.

Tarea 3 - areas de oportunidad para el ASESOR (coaching, no una evaluacion de cumplimiento):
- "mejora_llamada": 1 a 3 sugerencias concretas de que pudo hacer mejor en la llamada en general
  (tono, escucha activa, claridad, manejo de objeciones, etc). Si la llamada ya estuvo bien,
  puedes devolver una lista vacia.
- "cierre_negociacion": 1 a 3 sugerencias concretas y accionables de que pudo decir el asesor para
  cerrar mejor la negociacion (obtener un compromiso de pago, proponer un convenio, etc). Si el
  asesor ya cerro bien, puedes devolver una lista vacia.
Estas son recomendaciones, no afirmaciones sobre lo que paso -- no necesitan evidencia obligatoria.
Si mencionas un momento puntual de la llamada, incluye la cita y sus marcas de tiempo para dar
contexto; si no aplica a un momento especifico, deja esos campos en null.

Tarea 4 - resumen de la llamada:
- "who_answered": quien contesto la llamada. Usa exactamente una de estas etiquetas:
  {", ".join(WHO_ANSWERED)}. Si no puedes determinarlo, deja en null.
- "summary": un resumen corto (2 a 4 lineas) en espanol con lo mas importante de la llamada: con
  quien se hablo, el motivo, si se menciono un monto o una fecha de promesa de pago, y el resultado
  general. No repitas la transcripcion, sintetiza.

Reglas que debes seguir sin excepcion para el checklist, las emociones y who_answered (NO aplica a
la tarea 3 de coaching, ni al texto libre de "summary"):
- Si das una respuesta afirmativa (is_met true/false, o una etiqueta de emocion), DEBES incluir
  una cita textual corta y exacta tomada de la transcripcion y sus marcas de tiempo
  (start_ms/end_ms). Nunca inventes una cita que no este en la transcripcion.
- Si no encuentras evidencia clara, el campo correspondiente debe ser null (is_met o label), y su
  cita/tiempos tambien null.
- No agregues items de checklist que no esten en la lista dada.

Responde SOLO con JSON valido, sin texto alrededor, con este formato exacto:
{{"findings": [{{"checklist_item_code": "...", "is_met": true|false|null,
"evidence_quote": "..."|null, "evidence_start_ms": int|null, "evidence_end_ms": int|null,
"notes": "..."}}],
"agent_emotion": {{"label": "..."|null, "evidence_quote": "..."|null, "evidence_start_ms": int|null, "evidence_end_ms": int|null}},
"debtor_emotion": {{"label": "..."|null, "evidence_quote": "..."|null, "evidence_start_ms": int|null, "evidence_end_ms": int|null}},
"mejora_llamada": [{{"suggestion": "...", "reference_quote": "..."|null, "reference_start_ms": int|null, "reference_end_ms": int|null}}],
"cierre_negociacion": [{{"suggestion": "...", "reference_quote": "..."|null, "reference_start_ms": int|null, "reference_end_ms": int|null}}],
"who_answered": {{"label": "..."|null, "evidence_quote": "..."|null, "evidence_start_ms": int|null, "evidence_end_ms": int|null}},
"summary": "..."|null}}
"""


def _build_transcript_text(segments: list[dict]) -> str:
    return "\n".join(f"[{seg['start_ms']}-{seg['end_ms']}ms] {seg['text']}" for seg in segments)


def _build_checklist_text(checklist_items: list[dict]) -> str:
    lines = []
    for item in checklist_items:
        risk = " (PRACTICA PROHIBIDA / RIESGO LEGAL)" if item["is_legal_risk"] else ""
        lines.append(f"- {item['code']}: {item['label']}{risk}")
    return "\n".join(lines)


def _label_with_evidence(raw_value: dict, allowed_labels: tuple[str, ...]) -> dict:
    """Regla #4: una etiqueta (emocion, quien contesto) solo se conserva si
    trae cita + marcas de tiempo completas y esta dentro del catalogo
    permitido. Usado tanto para emociones como para who_answered."""
    raw_value = raw_value or {}
    label = raw_value.get("label")
    quote = raw_value.get("evidence_quote")
    start_ms = raw_value.get("evidence_start_ms")
    end_ms = raw_value.get("evidence_end_ms")

    has_evidence = quote is not None and start_ms is not None and end_ms is not None
    if label not in allowed_labels or not has_evidence:
        label, quote, start_ms, end_ms = None, None, None, None

    return {"label": label, "quote": quote, "start_ms": start_ms, "end_ms": end_ms}


def _parse_suggestions(raw_list, category: str) -> list[dict]:
    suggestions = []
    for raw in raw_list or []:
        suggestion_text = (raw or {}).get("suggestion")
        if not suggestion_text:
            continue
        quote = raw.get("reference_quote")
        start_ms = raw.get("reference_start_ms")
        end_ms = raw.get("reference_end_ms")
        # Consistencia (no "evidencia obligatoria" como en los findings):
        # si hay cita, tiene que traer sus dos marcas de tiempo o se descarta
        # la cita (la sugerencia en si se conserva).
        if quote is None or start_ms is None or end_ms is None:
            quote, start_ms, end_ms = None, None, None
        suggestions.append(
            {
                "category": category,
                "suggestion": suggestion_text,
                "reference_quote": quote,
                "reference_start_ms": start_ms,
                "reference_end_ms": end_ms,
            }
        )
    return suggestions


def analyze(
    client, segments: list[dict], checklist_items: list[dict], call_script: str | None = None
) -> tuple[list[dict], dict, dict, list[dict], dict, str]:
    """checklist_items: [{"checklist_item_id": uuid, "code": str, "label": str,
    "is_legal_risk": bool}, ...]

    call_script: texto libre opcional (app.clients.call_script) con todo lo
    que la llamada deberia cubrir segun Calidad -- se agrega como contexto
    extra al prompt, ademas del checklist puntual, pero no genera findings
    propios (esos siguen viniendo solo del checklist, con evidencia).

    Devuelve (findings, agent_emotion, debtor_emotion, coaching_suggestions,
    call_summary, raw_response_text).

    call_summary: {"who_answered": str|None, "who_answered_quote": str|None,
    "who_answered_start_ms": int|None, "who_answered_end_ms": int|None,
    "summary": str|None}.

    findings trae una fila por cada checklist_item (aunque el modelo no haya
    dicho nada de el, en cuyo caso queda is_met=None): asi la pantalla
    siempre puede mostrar el checklist completo, no solo lo que el modelo
    decidio contestar.

    agent_emotion/debtor_emotion: {"label": str|None, "quote": str|None,
    "start_ms": int|None, "end_ms": int|None}.

    raw_response_text es el JSON tal como lo devolvio el modelo, para
    guardarlo sin tocar en app.call_analyses.raw_response (regla #3: el
    resultado original de la IA se conserva siempre).
    """
    script_block = (
        f"Instrucciones generales del cliente sobre como debe ser la llamada completa:\n"
        f"{call_script.strip()}\n\n"
        if call_script and call_script.strip()
        else ""
    )
    user_prompt = (
        f"{script_block}"
        f"Checklist a evaluar:\n{_build_checklist_text(checklist_items)}\n\n"
        f"Transcripcion de la llamada:\n{_build_transcript_text(segments)}\n"
    )

    response = client.chat.completions.create(
        model=ANALYSIS_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw_text = response.choices[0].message.content
    raw = json.loads(raw_text)
    by_code = {f.get("checklist_item_code"): f for f in raw.get("findings", [])}

    findings = []
    for item in checklist_items:
        raw_finding = by_code.get(item["code"], {})
        is_met = raw_finding.get("is_met")
        quote = raw_finding.get("evidence_quote")
        start_ms = raw_finding.get("evidence_start_ms")
        end_ms = raw_finding.get("evidence_end_ms")

        # Regla #4, forzada en codigo ademas de en la base de datos: sin
        # evidencia completa no se puede afirmar nada.
        has_evidence = quote is not None and start_ms is not None and end_ms is not None
        if not has_evidence:
            is_met, quote, start_ms, end_ms = None, None, None, None

        findings.append(
            {
                "checklist_item_id": item["checklist_item_id"],
                "is_legal_risk": item["is_legal_risk"],
                "is_met": is_met,
                "evidence_quote": quote,
                "evidence_start_ms": start_ms,
                "evidence_end_ms": end_ms,
                "notes": raw_finding.get("notes"),
            }
        )

    agent_emotion = _label_with_evidence(raw.get("agent_emotion"), EMOTIONS)
    debtor_emotion = _label_with_evidence(raw.get("debtor_emotion"), EMOTIONS)

    coaching_suggestions = _parse_suggestions(
        raw.get("mejora_llamada"), "mejora_llamada"
    ) + _parse_suggestions(raw.get("cierre_negociacion"), "cierre_negociacion")

    who_answered = _label_with_evidence(raw.get("who_answered"), WHO_ANSWERED)
    call_summary = {
        "who_answered": who_answered["label"],
        "who_answered_quote": who_answered["quote"],
        "who_answered_start_ms": who_answered["start_ms"],
        "who_answered_end_ms": who_answered["end_ms"],
        "summary": raw.get("summary") or None,
    }

    return findings, agent_emotion, debtor_emotion, coaching_suggestions, call_summary, raw_text


def compute_score(findings: list[dict]) -> tuple[float | None, bool]:
    """Devuelve (score de 0 a 100 sobre los items evaluados que NO son de
    riesgo legal, hubo_riesgo_legal). Los items de riesgo legal no suman al
    score -- son una bandera aparte, no un punto mas del checklist."""
    non_risk = [f for f in findings if not f["is_legal_risk"] and f["is_met"] is not None]
    met = [f for f in non_risk if f["is_met"] is True]
    score = round(len(met) / len(non_risk) * 100, 1) if non_risk else None

    legal_risk_triggered = any(f["is_legal_risk"] and f["is_met"] is True for f in findings)
    return score, legal_risk_triggered
