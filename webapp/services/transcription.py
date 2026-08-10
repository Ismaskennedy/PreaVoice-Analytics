from pathlib import Path

from webapp.config import TRANSCRIPTION_MODEL

# Umbrales empiricos sobre avg_logprob (que tan segura estuvo Whisper de
# cada fragmento, valores cercanos a 0 = mas seguro, mas negativo = menos
# seguro). No es una medicion acustica real (ruido, interferencia fisica);
# es una aproximacion a partir de la confianza del propio transcriptor.
QUALITY_THRESHOLDS = {"buena": -0.35, "regular": -0.75}


def _get(obj, name):
    return getattr(obj, name) if hasattr(obj, name) else obj[name]


def transcribe(client, file_path: Path) -> tuple[list[dict], list[dict]]:
    """Transcribe un archivo de audio. Devuelve (segments, raw_segments):

    segments: [{"speaker": None, "start_ms": int, "end_ms": int, "text": str}, ...]
    raw_segments: los segmentos tal como los devolvio Whisper (con
    avg_logprob/no_speech_prob), para poder estimar la calidad de audio
    despues sin tener que llamar a la API otra vez.

    Whisper no distingue quien habla (speaker queda en None); si mas
    adelante hace falta diarizacion, es un cambio en este archivo nada mas.
    """
    with open(file_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL,
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    raw_segments = list(response.segments or [])
    segments = []
    for segment in raw_segments:
        text = _get(segment, "text").strip()
        if not text:
            continue
        segments.append(
            {
                "speaker": None,
                "start_ms": round(_get(segment, "start") * 1000),
                "end_ms": round(_get(segment, "end") * 1000),
                "text": text,
            }
        )
    return segments, raw_segments


def assess_audio_quality(raw_segments: list[dict]) -> dict:
    """Estima la calidad del audio a partir de avg_logprob (confianza de
    Whisper por fragmento). Devuelve {"label": "buena"|"regular"|"mala"|None,
    "notes": str}. label es None si no hay segmentos que evaluar."""
    scored = [
        (_get(seg, "avg_logprob"), _get(seg, "start"), _get(seg, "end"))
        for seg in raw_segments
        if _get(seg, "text").strip()
    ]
    if not scored:
        return {"label": None, "notes": "No hay segmentos con texto para evaluar la calidad del audio."}

    avg_logprob_mean = sum(s[0] for s in scored) / len(scored)
    if avg_logprob_mean >= QUALITY_THRESHOLDS["buena"]:
        label = "buena"
    elif avg_logprob_mean >= QUALITY_THRESHOLDS["regular"]:
        label = "regular"
    else:
        label = "mala"

    low_confidence = [(start, end) for logprob, start, end in scored if logprob < QUALITY_THRESHOLDS["regular"]]
    notes = f"Confianza promedio de transcripcion: {avg_logprob_mean:.2f}."
    if low_confidence:
        rango = ", ".join(f"{round(s * 1000)}-{round(e * 1000)}ms" for s, e in low_confidence[:5])
        notes += f" Fragmentos con baja confianza (posible ruido/interferencia): {rango}."

    return {"label": label, "notes": notes}
