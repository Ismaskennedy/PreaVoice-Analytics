"""Valida la regla #7 (la IA propone, el humano dispone): una evaluacion
'confirmed' necesita quien y cuando la confirmo; una en borrador no puede
tener esos datos. Tambien valida que las correcciones son append-only de
verdad (un UPDATE o DELETE los rechaza) y que solo hay una evaluacion
vigente por llamada."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError


def _make_context(conn, label):
    """Arma tenant, cliente, gestor, llamada, transcripcion, checklist item,
    un analisis con un hallazgo, y un revisor de Calidad. Devuelve
    (tenant_id, call_id, analysis_id, finding_id, reviewer_id)."""
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
    reviewer_id = uuid.uuid4()
    conn.execute(
        text("INSERT INTO app.users (id, tenant_id, email, full_name) VALUES (:id, :tenant_id, :email, :full_name)"),
        {"id": reviewer_id, "tenant_id": tenant_id, "email": f"calidad-{label}@example.com", "full_name": f"Calidad {label}"},
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

    analysis_id = conn.execute(
        text(
            "INSERT INTO app.call_analyses (tenant_id, call_id, transcript_id, model, prompt_version, raw_response) "
            "VALUES (:tenant_id, :call_id, :transcript_id, 'gpt-demo', 'v1', '{}'::jsonb) "
            "RETURNING id"
        ),
        {"tenant_id": tenant_id, "call_id": call_id, "transcript_id": transcript_id},
    ).scalar_one()

    finding_id = conn.execute(
        text(
            "INSERT INTO app.call_analysis_findings "
            "(tenant_id, analysis_id, checklist_item_id, is_met, evidence_start_ms, evidence_end_ms, evidence_quote) "
            "VALUES (:tenant_id, :analysis_id, :checklist_item_id, true, 0, 1000, 'evidencia') "
            "RETURNING id"
        ),
        {"tenant_id": tenant_id, "analysis_id": analysis_id, "checklist_item_id": checklist_item_id},
    ).scalar_one()

    return tenant_id, call_id, analysis_id, finding_id, reviewer_id


def _insert_evaluation(conn, tenant_id, call_id, analysis_id, status="draft", reviewer_id=None):
    if status == "confirmed":
        return conn.execute(
            text(
                "INSERT INTO app.call_evaluations "
                "(tenant_id, call_id, analysis_id, status, confirmed_by, confirmed_at) "
                "VALUES (:tenant_id, :call_id, :analysis_id, 'confirmed', :reviewer_id, now()) "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "analysis_id": analysis_id, "reviewer_id": reviewer_id},
        ).scalar_one()
    return conn.execute(
        text(
            "INSERT INTO app.call_evaluations (tenant_id, call_id, analysis_id) "
            "VALUES (:tenant_id, :call_id, :analysis_id) RETURNING id"
        ),
        {"tenant_id": tenant_id, "call_id": call_id, "analysis_id": analysis_id},
    ).scalar_one()


def test_confirmed_evaluation_requires_reviewer_and_timestamp(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, analysis_id, _finding_id, _reviewer_id = _make_context(conn, "confirmar-sin-datos")
            conn.execute(
                text(
                    "INSERT INTO app.call_evaluations (tenant_id, call_id, analysis_id, status) "
                    "VALUES (:tenant_id, :call_id, :analysis_id, 'confirmed')"
                ),
                {"tenant_id": tenant_id, "call_id": call_id, "analysis_id": analysis_id},
            )


def test_draft_evaluation_cannot_have_reviewer(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, analysis_id, _finding_id, reviewer_id = _make_context(conn, "borrador-con-datos")
            conn.execute(
                text(
                    "INSERT INTO app.call_evaluations (tenant_id, call_id, analysis_id, status, confirmed_by) "
                    "VALUES (:tenant_id, :call_id, :analysis_id, 'draft', :reviewer_id)"
                ),
                {"tenant_id": tenant_id, "call_id": call_id, "analysis_id": analysis_id, "reviewer_id": reviewer_id},
            )


def test_confirmed_evaluation_with_reviewer_and_timestamp_is_accepted(engine):
    with engine.begin() as conn:
        tenant_id, call_id, analysis_id, _finding_id, reviewer_id = _make_context(conn, "confirmar-bien")
        evaluation_id = _insert_evaluation(conn, tenant_id, call_id, analysis_id, status="confirmed", reviewer_id=reviewer_id)

        status = conn.execute(
            text("SELECT status FROM app.call_evaluations WHERE id = :id"), {"id": evaluation_id}
        ).scalar_one()

    assert status == "confirmed"


def test_only_one_current_evaluation_per_call(engine):
    with engine.begin() as conn:
        tenant_id, call_id, analysis_id, _finding_id, _reviewer_id = _make_context(conn, "vigente-unico")
        _insert_evaluation(conn, tenant_id, call_id, analysis_id)

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            _insert_evaluation(conn, tenant_id, call_id, analysis_id)


def test_correction_requires_a_change(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, call_id, analysis_id, finding_id, reviewer_id = _make_context(conn, "correccion-vacia")
            evaluation_id = _insert_evaluation(conn, tenant_id, call_id, analysis_id)
            conn.execute(
                text(
                    "INSERT INTO app.call_evaluation_corrections "
                    "(tenant_id, evaluation_id, finding_id, reason, corrected_by) "
                    "VALUES (:tenant_id, :evaluation_id, :finding_id, 'sin cambio real', :reviewer_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "evaluation_id": evaluation_id,
                    "finding_id": finding_id,
                    "reviewer_id": reviewer_id,
                },
            )


def test_correction_is_append_only(engine):
    with engine.begin() as conn:
        tenant_id, call_id, analysis_id, finding_id, reviewer_id = _make_context(conn, "append-only")
        evaluation_id = _insert_evaluation(conn, tenant_id, call_id, analysis_id)
        correction_id = conn.execute(
            text(
                "INSERT INTO app.call_evaluation_corrections "
                "(tenant_id, evaluation_id, finding_id, previous_is_met, corrected_is_met, reason, corrected_by) "
                "VALUES (:tenant_id, :evaluation_id, :finding_id, true, false, 'revision manual', :reviewer_id) "
                "RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "evaluation_id": evaluation_id,
                "finding_id": finding_id,
                "reviewer_id": reviewer_id,
            },
        ).scalar_one()

    with pytest.raises(ProgrammingError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(
                text("UPDATE app.call_evaluation_corrections SET reason = 'editado' WHERE id = :id"),
                {"id": correction_id},
            )

    with pytest.raises(ProgrammingError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(
                text("DELETE FROM app.call_evaluation_corrections WHERE id = :id"),
                {"id": correction_id},
            )
