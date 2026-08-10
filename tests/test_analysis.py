"""Valida la regla #4: un hallazgo de la IA solo puede afirmar algo (is_met)
si trae evidencia (cita + marcas de tiempo); si no, se anula. Tambien valida
que solo puede haber un analisis vigente por llamada."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _make_context(conn, label):
    """Arma la cadena completa que necesita un analisis: tenant, cliente,
    gestor asignado, llamada, transcripcion, y un item de checklist.
    Devuelve (tenant_id, call_id, transcript_id, checklist_item_id)."""
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    conn.execute(
        text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tenant_id, "name": f"Tenant {label}", "slug": f"tenant-{label}-{tenant_id}"},
    )
    conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
    conn.execute(
        text("INSERT INTO app.users (id, tenant_id, email, full_name) VALUES (:id, :tenant_id, :email, :full_name)"),
        {"id": agent_id, "tenant_id": tenant_id, "email": f"agente-{label}@example.com", "full_name": f"Agente {label}"},
    )
    conn.execute(
        text("INSERT INTO app.clients (id, tenant_id, name, code) VALUES (:id, :tenant_id, :name, :code)"),
        {"id": client_id, "tenant_id": tenant_id, "name": f"Cliente {label}", "code": f"cliente-{label}"},
    )
    conn.execute(
        text(
            "INSERT INTO app.agent_assignments (tenant_id, user_id, client_id) "
            "VALUES (:tenant_id, :user_id, :client_id)"
        ),
        {"tenant_id": tenant_id, "user_id": agent_id, "client_id": client_id},
    )

    call_id = conn.execute(
        text(
            "INSERT INTO app.calls (tenant_id, agent_id, uploaded_by) "
            "VALUES (:tenant_id, :agent_id, :agent_id) RETURNING id"
        ),
        {"tenant_id": tenant_id, "agent_id": agent_id},
    ).scalar_one()

    transcript_id = conn.execute(
        text(
            "INSERT INTO app.transcripts (tenant_id, call_id, provider) "
            "VALUES (:tenant_id, :call_id, 'deepgram') RETURNING id"
        ),
        {"tenant_id": tenant_id, "call_id": call_id},
    ).scalar_one()

    category_id = conn.execute(
        text(
            "INSERT INTO app.taxonomy_categories (tenant_id, code, name) "
            "VALUES (:tenant_id, :code, :name) RETURNING id"
        ),
        {"tenant_id": tenant_id, "code": f"categoria-{label}", "name": f"Categoria {label}"},
    ).scalar_one()

    value_id = conn.execute(
        text(
            "INSERT INTO app.taxonomy_values (tenant_id, category_id, code, label) "
            "VALUES (:tenant_id, :category_id, :code, :label) RETURNING id"
        ),
        {"tenant_id": tenant_id, "category_id": category_id, "code": f"valor-{label}", "label": f"Valor {label}"},
    ).scalar_one()

    checklist_id = conn.execute(
        text(
            "INSERT INTO app.checklists (tenant_id, client_id, name) "
            "VALUES (:tenant_id, :client_id, :name) RETURNING id"
        ),
        {"tenant_id": tenant_id, "client_id": client_id, "name": f"Checklist {label}"},
    ).scalar_one()

    checklist_item_id = conn.execute(
        text(
            "INSERT INTO app.checklist_items (tenant_id, checklist_id, taxonomy_value_id) "
            "VALUES (:tenant_id, :checklist_id, :taxonomy_value_id) RETURNING id"
        ),
        {"tenant_id": tenant_id, "checklist_id": checklist_id, "taxonomy_value_id": value_id},
    ).scalar_one()

    return tenant_id, call_id, transcript_id, checklist_item_id


def _insert_analysis(conn, tenant_id, call_id, transcript_id):
    return conn.execute(
        text(
            "INSERT INTO app.call_analyses (tenant_id, call_id, transcript_id, model, prompt_version, raw_response) "
            "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb) "
            "RETURNING id"
        ),
        {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
    ).scalar_one()


def test_finding_with_claim_requires_evidence(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, checklist_item_id = _make_context(conn, "sin-evidencia")
            analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
            conn.execute(
                text(
                    "INSERT INTO app.call_analysis_findings (tenant_id, analysis_id, checklist_item_id, is_met) "
                    "VALUES (:tenant_id, :analysis_id, :checklist_item_id, true)"
                ),
                {"tenant_id": tenant_id, "analysis_id": analysis_id, "checklist_item_id": checklist_item_id},
            )


def test_finding_with_evidence_is_accepted(engine):
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, checklist_item_id = _make_context(conn, "con-evidencia")
        analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
        conn.execute(
            text(
                "INSERT INTO app.call_analysis_findings "
                "(tenant_id, analysis_id, checklist_item_id, is_met, evidence_start_ms, evidence_end_ms, evidence_quote) "
                "VALUES (:tenant_id, :analysis_id, :checklist_item_id, true, 1000, 4500, 'le informo el monto total')"
            ),
            {"tenant_id": tenant_id, "analysis_id": analysis_id, "checklist_item_id": checklist_item_id},
        )

        is_met = conn.execute(
            text("SELECT is_met FROM app.call_analysis_findings WHERE analysis_id = :id"),
            {"id": analysis_id},
        ).scalar_one()

    assert is_met is True


def test_finding_without_claim_does_not_need_evidence(engine):
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, checklist_item_id = _make_context(conn, "sin-afirmacion")
        analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
        # is_met en NULL: "no se pudo determinar". No trae evidencia y esta bien.
        conn.execute(
            text(
                "INSERT INTO app.call_analysis_findings (tenant_id, analysis_id, checklist_item_id) "
                "VALUES (:tenant_id, :analysis_id, :checklist_item_id)"
            ),
            {"tenant_id": tenant_id, "analysis_id": analysis_id, "checklist_item_id": checklist_item_id},
        )


def test_only_one_current_analysis_per_call(engine):
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "vigente-unico")
        _insert_analysis(conn, tenant_id, call_id, transcript_id)

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            _insert_analysis(conn, tenant_id, call_id, transcript_id)

    # Al marcar el primero SUPERSEDED, si se puede crear uno nuevo vigente.
    with engine.begin() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
        conn.execute(
            text("UPDATE app.call_analyses SET state = 'SUPERSEDED' WHERE call_id = :call_id"),
            {"call_id": call_id},
        )
        _insert_analysis(conn, tenant_id, call_id, transcript_id)
        current_count = conn.execute(
            text("SELECT count(*) FROM app.call_analyses WHERE call_id = :call_id AND state = 'CURRENT'"),
            {"call_id": call_id},
        ).scalar_one()

    assert current_count == 1


def test_emotion_with_label_requires_full_evidence(engine):
    """Mismo principio que las findings del checklist (regla #4), aplicado
    a agent_emotion/debtor_emotion."""
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "emocion-sin-evidencia")
            conn.execute(
                text(
                    "INSERT INTO app.call_analyses "
                    "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, agent_emotion) "
                    "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, 'frustrado')"
                ),
                {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
            )


def test_emotion_with_full_evidence_is_accepted(engine):
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "emocion-con-evidencia")
        conn.execute(
            text(
                "INSERT INTO app.call_analyses "
                "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, "
                "debtor_emotion, debtor_emotion_quote, debtor_emotion_start_ms, debtor_emotion_end_ms) "
                "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, "
                "'ansioso', 'no se que hacer', 1000, 2000)"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
        )

        debtor_emotion = conn.execute(
            text("SELECT debtor_emotion FROM app.call_analyses WHERE call_id = :call_id"), {"call_id": call_id}
        ).scalar_one()

    assert debtor_emotion == "ansioso"


def test_emotion_label_outside_allowed_set_is_rejected(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "emocion-invalida")
            conn.execute(
                text(
                    "INSERT INTO app.call_analyses "
                    "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, "
                    "agent_emotion, agent_emotion_quote, agent_emotion_start_ms, agent_emotion_end_ms) "
                    "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, "
                    "'furioso', 'algo', 0, 100)"
                ),
                {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
            )


def test_coaching_suggestion_without_reference_is_accepted(engine):
    """A diferencia de los findings, una sugerencia de coaching no necesita
    evidencia -- es una recomendacion, no una afirmacion sobre la llamada."""
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "sugerencia-sin-cita")
        analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
        conn.execute(
            text(
                "INSERT INTO app.call_coaching_suggestions (tenant_id, analysis_id, category, suggestion) "
                "VALUES (:tenant_id, :analysis_id, 'mejora_llamada', 'Hablar mas despacio')"
            ),
            {"tenant_id": tenant_id, "analysis_id": analysis_id},
        )

        suggestion = conn.execute(
            text("SELECT suggestion FROM app.call_coaching_suggestions WHERE analysis_id = :id"),
            {"id": analysis_id},
        ).scalar_one()

    assert suggestion == "Hablar mas despacio"


def test_coaching_suggestion_with_quote_requires_timestamps(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "sugerencia-cita-incompleta")
            analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
            conn.execute(
                text(
                    "INSERT INTO app.call_coaching_suggestions "
                    "(tenant_id, analysis_id, category, suggestion, reference_quote) "
                    "VALUES (:tenant_id, :analysis_id, 'cierre_negociacion', 'Proponer convenio', 'una cita')"
                ),
                {"tenant_id": tenant_id, "analysis_id": analysis_id},
            )


def test_coaching_suggestion_category_outside_allowed_set_is_rejected(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "categoria-invalida")
            analysis_id = _insert_analysis(conn, tenant_id, call_id, transcript_id)
            conn.execute(
                text(
                    "INSERT INTO app.call_coaching_suggestions (tenant_id, analysis_id, category, suggestion) "
                    "VALUES (:tenant_id, :analysis_id, 'otra_cosa', 'lo que sea')"
                ),
                {"tenant_id": tenant_id, "analysis_id": analysis_id},
            )


def test_who_answered_requires_full_evidence(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "contesto-sin-evidencia")
            conn.execute(
                text(
                    "INSERT INTO app.call_analyses "
                    "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, who_answered) "
                    "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, 'titular')"
                ),
                {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
            )


def test_who_answered_with_evidence_and_summary_is_accepted(engine):
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "contesto-con-evidencia")
        conn.execute(
            text(
                "INSERT INTO app.call_analyses "
                "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, "
                "who_answered, who_answered_quote, who_answered_start_ms, who_answered_end_ms, summary) "
                "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, "
                "'familiar', 'no esta, soy su hermano', 0, 300, 'Se hablo con un familiar del titular.')"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
        )

        row = conn.execute(
            text("SELECT who_answered, summary FROM app.call_analyses WHERE call_id = :call_id"),
            {"call_id": call_id},
        ).mappings().one()

    assert row["who_answered"] == "familiar"
    assert row["summary"] == "Se hablo con un familiar del titular."


def test_summary_without_who_answered_does_not_need_evidence(engine):
    """El resumen es una sintesis de toda la llamada, no una afirmacion
    puntual -- no exige el mismo CHECK que who_answered/emociones/findings."""
    with engine.begin() as conn:
        tenant_id, call_id, transcript_id, _item_id = _make_context(conn, "resumen-sin-quien-contesto")
        conn.execute(
            text(
                "INSERT INTO app.call_analyses "
                "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, summary) "
                "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb, "
                "'No se pudo determinar con quien se hablo.')"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
        )
