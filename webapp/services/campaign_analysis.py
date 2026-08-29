"""Deteccion de campaña por IA (GPT-4o-mini) -- capa aparte del reporte
por palabra clave (webapp/services/reports.py), que es gratis pero se
pierde variantes foneticas que Whisper transcribe mal ("espin", "es pin").
Este si entiende esas variantes, y de paso extrae telefono/nombre del
gestor/nombre del cliente mencionados durante la llamada."""
import json

from webapp.config import ANALYSIS_MODEL

CAMPANAS = ("SPIN", "BRADESCARD", "AZTECA", "INVEX", "OTRA", "NO_DETERMINADA")
CONOCIDAS = ("SPIN", "BRADESCARD", "AZTECA", "INVEX")
CONFIANZAS = ("alta", "media", "baja")

SYSTEM_PROMPT = """Analiza esta transcripcion de una llamada de cobranza y responde en JSON:
{"campana": "SPIN"|"BRADESCARD"|"AZTECA"|"INVEX"|"OTRA"|"NO_DETERMINADA",
"confianza": "alta"|"media"|"baja",
"evidencia": "la frase exacta que lo indica"|null,
"telefono_mencionado": "numero si el agente lo dicta o confirma"|null,
"nombre_gestor": "si se presenta por nombre"|null,
"nombre_cliente": "si lo menciona"|null}

SPIN puede aparecer transcrito como: spin, espin, es pin, spim, esbin, SPI.
BRADESCARD, AZTECA (Banco Azteca) e INVEX tambien pueden venir con errores
de transcripcion parecidos -- usa el contexto de cobranza/tarjeta/banco
para reconocerlos aunque la palabra exacta este deformada.

Usa "OTRA" si la llamada menciona una campaña/cliente distinto a estas
cuatro. Usa "NO_DETERMINADA" si no hay suficiente informacion.

Considera tambien el contexto: si el agente menciona un numero de telefono
o un nombre de cliente, extraelo aunque no diga la campaña.

Regla obligatoria: si "campana" es SPIN, BRADESCARD, AZTECA o INVEX, DEBES
incluir en "evidencia" la frase textual exacta de la transcripcion que lo
indica -- nunca inventes una cita que no este ahi. Si no tienes una cita
real, usa "OTRA" o "NO_DETERMINADA" en vez de forzar una de las cuatro
campañas conocidas.

Responde SOLO con JSON valido, sin texto alrededor."""


def analyze_campaign(client, transcript_text: str) -> dict:
    """Devuelve {"campana", "confianza", "evidencia", "telefono_mencionado",
    "nombre_gestor", "nombre_cliente", "raw_response"}.

    Regla #4 forzada en codigo (no solo en el prompt): una de las 4
    campañas conocidas sin evidencia se degrada a NO_DETERMINADA -- no
    confiamos en que el modelo obedezca el prompt."""
    response = client.chat.completions.create(
        model=ANALYSIS_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcripcion:\n{transcript_text}"},
        ],
    )
    raw_text = response.choices[0].message.content
    raw = json.loads(raw_text)

    campana = raw.get("campana")
    evidencia = raw.get("evidencia") or None
    confianza = raw.get("confianza")

    if campana not in CAMPANAS:
        campana = "NO_DETERMINADA"
    if campana in CONOCIDAS and not evidencia:
        campana = "NO_DETERMINADA"
        confianza = "baja"
    if confianza not in CONFIANZAS:
        confianza = None
    if campana in ("OTRA", "NO_DETERMINADA"):
        evidencia = evidencia or None

    return {
        "campana": campana,
        "confianza": confianza,
        "evidencia": evidencia,
        "telefono_mencionado": raw.get("telefono_mencionado") or None,
        "nombre_gestor": raw.get("nombre_gestor") or None,
        "nombre_cliente": raw.get("nombre_cliente") or None,
        "raw_response": raw_text,
    }
