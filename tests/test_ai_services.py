"""Pruebas de webapp/services/ con un cliente de OpenAI simulado: no
llaman a la API real (no cuesta dinero, no necesitan OPENAI_API_KEY, no
dependen de la red)."""
import json
import uuid
from types import SimpleNamespace

from webapp.services.analysis import analyze, compute_score
from webapp.services.transcription import assess_audio_quality, transcribe


class FakeTranscriptionClient:
    def __init__(self, segments):
        self._segments = segments
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(segments=self._segments))
        )


class FakeChatClient:
    def __init__(self, content: str):
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        response = SimpleNamespace(choices=[choice])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))


def _segment(text, start, end, avg_logprob=-0.2):
    return SimpleNamespace(text=text, start=start, end=end, avg_logprob=avg_logprob)


def test_transcribe_converts_seconds_to_milliseconds(tmp_path):
    fake_segments = [
        _segment(" Hola, buenas tardes. ", 0.0, 2.5),
        _segment("", 2.5, 2.6),  # segmento vacio: se descarta
        _segment("Le hablo de su cuenta.", 2.6, 5.125),
    ]
    client = FakeTranscriptionClient(fake_segments)
    audio_path = tmp_path / "llamada.mp3"
    audio_path.write_bytes(b"contenido falso")

    segments, raw_segments = transcribe(client, audio_path)

    assert segments == [
        {"speaker": None, "start_ms": 0, "end_ms": 2500, "text": "Hola, buenas tardes."},
        {"speaker": None, "start_ms": 2600, "end_ms": 5125, "text": "Le hablo de su cuenta."},
    ]
    assert raw_segments == fake_segments


def test_assess_audio_quality_good_when_high_confidence():
    raw_segments = [_segment("Hola", 0.0, 1.0, avg_logprob=-0.1), _segment("Buenas tardes", 1.0, 2.0, avg_logprob=-0.2)]

    quality = assess_audio_quality(raw_segments)

    assert quality["label"] == "buena"
    assert "no se pudo" not in quality["notes"].lower()


def test_assess_audio_quality_bad_when_low_confidence():
    raw_segments = [_segment("ruido", 0.0, 1.0, avg_logprob=-1.5), _segment("mas ruido", 1.0, 2.0, avg_logprob=-1.8)]

    quality = assess_audio_quality(raw_segments)

    assert quality["label"] == "mala"
    assert "0-1000ms" in quality["notes"] or "0-1" in quality["notes"]


def test_assess_audio_quality_none_without_segments():
    quality = assess_audio_quality([])
    assert quality["label"] is None


def test_analyze_keeps_evidence_and_defaults_missing_items_to_null():
    checklist_items = [
        {"checklist_item_id": uuid.uuid4(), "code": "informar_monto", "label": "Informo el monto", "is_legal_risk": False},
        {"checklist_item_id": uuid.uuid4(), "code": "amenaza", "label": "No amenazo", "is_legal_risk": True},
    ]
    model_response = json.dumps(
        {
            "findings": [
                {
                    "checklist_item_code": "informar_monto",
                    "is_met": True,
                    "evidence_quote": "su saldo es de 5000 pesos",
                    "evidence_start_ms": 1000,
                    "evidence_end_ms": 3000,
                    "notes": "lo dijo claro",
                }
                # 'amenaza' no viene en la respuesta del modelo a proposito.
            ],
            "agent_emotion": None,
            "debtor_emotion": None,
        }
    )
    client = FakeChatClient(model_response)

    findings, _agent_emotion, _debtor_emotion, _suggestions, _summary, raw_text = analyze(client, segments=[], checklist_items=checklist_items)

    assert raw_text == model_response
    by_item = {f["checklist_item_id"]: f for f in findings}

    informar = by_item[checklist_items[0]["checklist_item_id"]]
    assert informar["is_met"] is True
    assert informar["evidence_quote"] == "su saldo es de 5000 pesos"

    amenaza = by_item[checklist_items[1]["checklist_item_id"]]
    assert amenaza["is_met"] is None
    assert amenaza["evidence_quote"] is None


def test_analyze_nulls_out_claim_without_evidence():
    """Regla #4 forzada en codigo: si el modelo dice is_met=true pero no
    trae cita, se anula, igual que lo exige el CHECK de la base de datos."""
    checklist_items = [
        {"checklist_item_id": uuid.uuid4(), "code": "ofrecer_convenio", "label": "Ofrecio convenio", "is_legal_risk": False},
    ]
    model_response = json.dumps(
        {"findings": [{"checklist_item_code": "ofrecer_convenio", "is_met": True, "evidence_quote": None}]}
    )
    client = FakeChatClient(model_response)

    findings, _agent_emotion, _debtor_emotion, _suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=checklist_items)

    assert findings[0]["is_met"] is None
    assert findings[0]["evidence_quote"] is None


def test_analyze_keeps_emotion_with_full_evidence():
    model_response = json.dumps(
        {
            "findings": [],
            "agent_emotion": {
                "label": "empatico",
                "evidence_quote": "entiendo su situacion",
                "evidence_start_ms": 500,
                "evidence_end_ms": 1500,
            },
            "debtor_emotion": {"label": "ansioso", "evidence_quote": "no se que hacer", "evidence_start_ms": 2000, "evidence_end_ms": 2800},
        }
    )
    client = FakeChatClient(model_response)

    _findings, agent_emotion, debtor_emotion, _suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=[])

    assert agent_emotion == {"label": "empatico", "quote": "entiendo su situacion", "start_ms": 500, "end_ms": 1500}
    assert debtor_emotion["label"] == "ansioso"


def test_analyze_nulls_out_emotion_without_evidence():
    model_response = json.dumps(
        {"findings": [], "agent_emotion": {"label": "frustrado", "evidence_quote": None}, "debtor_emotion": None}
    )
    client = FakeChatClient(model_response)

    _findings, agent_emotion, debtor_emotion, _suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=[])

    assert agent_emotion == {"label": None, "quote": None, "start_ms": None, "end_ms": None}
    assert debtor_emotion == {"label": None, "quote": None, "start_ms": None, "end_ms": None}


def test_analyze_nulls_out_emotion_with_unknown_label():
    """Si el modelo inventa una etiqueta que no esta en la lista permitida,
    se anula -- mismo espiritu que el CHECK de la base de datos."""
    model_response = json.dumps(
        {
            "findings": [],
            "agent_emotion": {
                "label": "furioso",  # no esta en EMOTIONS
                "evidence_quote": "algo",
                "evidence_start_ms": 0,
                "evidence_end_ms": 100,
            },
            "debtor_emotion": None,
        }
    )
    client = FakeChatClient(model_response)

    _findings, agent_emotion, _debtor_emotion, _suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=[])

    assert agent_emotion["label"] is None


def test_analyze_parses_coaching_suggestions_with_and_without_reference():
    model_response = json.dumps(
        {
            "findings": [],
            "agent_emotion": None,
            "debtor_emotion": None,
            "mejora_llamada": [
                {
                    "suggestion": "Hablar mas despacio al explicar el monto",
                    "reference_quote": "su saldo es de cinco mil pesos",
                    "reference_start_ms": 1000,
                    "reference_end_ms": 2000,
                },
                {"suggestion": "Confirmar datos de contacto al inicio", "reference_quote": None},
            ],
            "cierre_negociacion": [
                {"suggestion": "Ofrecer explicitamente una fecha limite de pago"},
            ],
        }
    )
    client = FakeChatClient(model_response)

    _findings, _agent_emotion, _debtor_emotion, suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=[])

    mejora = [s for s in suggestions if s["category"] == "mejora_llamada"]
    cierre = [s for s in suggestions if s["category"] == "cierre_negociacion"]

    assert len(mejora) == 2
    assert mejora[0]["reference_quote"] == "su saldo es de cinco mil pesos"
    # La segunda sugerencia no trae marcas de tiempo junto con la cita: se
    # descarta la cita (la sugerencia en si se conserva, no es evidencia
    # obligatoria como en los findings).
    assert mejora[1]["reference_quote"] is None
    assert mejora[1]["suggestion"] == "Confirmar datos de contacto al inicio"

    assert len(cierre) == 1
    assert cierre[0]["suggestion"] == "Ofrecer explicitamente una fecha limite de pago"


def test_analyze_returns_no_suggestions_when_call_was_fine():
    model_response = json.dumps(
        {"findings": [], "agent_emotion": None, "debtor_emotion": None, "mejora_llamada": [], "cierre_negociacion": []}
    )
    client = FakeChatClient(model_response)

    _findings, _agent_emotion, _debtor_emotion, suggestions, _summary, _raw = analyze(client, segments=[], checklist_items=[])

    assert suggestions == []


def test_analyze_keeps_who_answered_with_full_evidence_and_summary():
    model_response = json.dumps(
        {
            "findings": [],
            "agent_emotion": None,
            "debtor_emotion": None,
            "who_answered": {
                "label": "titular",
                "evidence_quote": "si, soy yo",
                "evidence_start_ms": 100,
                "evidence_end_ms": 400,
            },
            "summary": "El titular confirmo su identidad y se le informo el saldo pendiente.",
        }
    )
    client = FakeChatClient(model_response)

    _findings, _agent_emotion, _debtor_emotion, _suggestions, call_summary, _raw = analyze(
        client, segments=[], checklist_items=[]
    )

    assert call_summary["who_answered"] == "titular"
    assert call_summary["who_answered_quote"] == "si, soy yo"
    assert call_summary["summary"] == "El titular confirmo su identidad y se le informo el saldo pendiente."


def test_analyze_nulls_out_who_answered_without_evidence_but_keeps_summary():
    model_response = json.dumps(
        {
            "findings": [],
            "agent_emotion": None,
            "debtor_emotion": None,
            "who_answered": {"label": "familiar", "evidence_quote": None},
            "summary": "No se pudo confirmar con quien se hablo.",
        }
    )
    client = FakeChatClient(model_response)

    _findings, _agent_emotion, _debtor_emotion, _suggestions, call_summary, _raw = analyze(
        client, segments=[], checklist_items=[]
    )

    assert call_summary["who_answered"] is None
    assert call_summary["who_answered_quote"] is None
    # El resumen en si no exige evidencia (es sintesis, no una afirmacion puntual).
    assert call_summary["summary"] == "No se pudo confirmar con quien se hablo."


def test_compute_score_ignores_legal_risk_items_and_flags_them_separately():
    findings = [
        {"is_legal_risk": False, "is_met": True},
        {"is_legal_risk": False, "is_met": False},
        {"is_legal_risk": False, "is_met": None},  # sin evidencia: no cuenta
        {"is_legal_risk": True, "is_met": True},  # se cumplio la practica prohibida
    ]

    score, legal_risk_triggered = compute_score(findings)

    assert score == 50.0  # 1 de 2 items evaluados (no de riesgo) cumplidos
    assert legal_risk_triggered is True


def test_compute_score_no_legal_risk_when_nothing_met():
    findings = [
        {"is_legal_risk": False, "is_met": True},
        {"is_legal_risk": True, "is_met": False},
        {"is_legal_risk": True, "is_met": None},
    ]

    score, legal_risk_triggered = compute_score(findings)

    assert score == 100.0
    assert legal_risk_triggered is False
