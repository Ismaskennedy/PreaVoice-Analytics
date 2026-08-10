"""Valida audit.activity_log (aislado por tenant y append-only, igual que
las correcciones) y ops.processing_jobs (a proposito SIN aislamiento por
tenant: un worker necesita ver trabajos de todos los clientes)."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError


def _make_tenant(conn, label):
    tenant_id = uuid.uuid4()
    conn.execute(
        text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tenant_id, "name": f"Tenant {label}", "slug": f"tenant-{label}-{tenant_id}"},
    )
    conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
    return tenant_id


def _make_call(conn, tenant_id, label):
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()
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
    return conn.execute(
        text(
            "INSERT INTO app.calls (tenant_id, agent_id, uploaded_by) "
            "VALUES (:tenant_id, :agent_id, :agent_id) RETURNING id"
        ),
        {"tenant_id": tenant_id, "agent_id": agent_id},
    ).scalar_one()


def test_activity_log_respects_tenant_isolation(engine):
    with engine.begin() as conn:
        tenant_a = _make_tenant(conn, "audit-a")
        entry_a = conn.execute(
            text(
                "INSERT INTO audit.activity_log (tenant_id, action, entity_type, entity_id) "
                "VALUES (:tenant_id, 'call.uploaded', 'call', :entity_id) RETURNING id"
            ),
            {"tenant_id": tenant_a, "entity_id": uuid.uuid4()},
        ).scalar_one()

    with engine.begin() as conn:
        tenant_b = _make_tenant(conn, "audit-b")
        entry_b = conn.execute(
            text(
                "INSERT INTO audit.activity_log (tenant_id, action, entity_type, entity_id) "
                "VALUES (:tenant_id, 'call.uploaded', 'call', :entity_id) RETURNING id"
            ),
            {"tenant_id": tenant_b, "entity_id": uuid.uuid4()},
        ).scalar_one()

    query = text("SELECT id FROM audit.activity_log WHERE id IN (:a, :b)")
    with engine.connect() as conn:
        assert conn.execute(query, {"a": entry_a, "b": entry_b}).scalars().all() == []

    with engine.connect() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_a})
        assert conn.execute(query, {"a": entry_a, "b": entry_b}).scalars().all() == [entry_a]


def test_activity_log_is_append_only(engine):
    with engine.begin() as conn:
        tenant_id = _make_tenant(conn, "audit-append-only")
        entry_id = conn.execute(
            text(
                "INSERT INTO audit.activity_log (tenant_id, action, entity_type, entity_id) "
                "VALUES (:tenant_id, 'call.uploaded', 'call', :entity_id) RETURNING id"
            ),
            {"tenant_id": tenant_id, "entity_id": uuid.uuid4()},
        ).scalar_one()

    with pytest.raises(ProgrammingError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(
                text("UPDATE audit.activity_log SET action = 'editado' WHERE id = :id"), {"id": entry_id}
            )

    with pytest.raises(ProgrammingError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(text("DELETE FROM audit.activity_log WHERE id = :id"), {"id": entry_id})


def test_processing_jobs_visible_across_tenants(engine):
    """A proposito NO hay aislamiento por tenant en esta tabla: un worker
    tiene que poder ver trabajos pendientes de todos los clientes."""
    with engine.begin() as conn:
        tenant_a = _make_tenant(conn, "jobs-a")
        call_a = _make_call(conn, tenant_a, "jobs-a")
        job_a = conn.execute(
            text(
                "INSERT INTO ops.processing_jobs (tenant_id, call_id, job_type) "
                "VALUES (:tenant_id, :call_id, 'transcribe') RETURNING id"
            ),
            {"tenant_id": tenant_a, "call_id": call_a},
        ).scalar_one()

    with engine.begin() as conn:
        tenant_b = _make_tenant(conn, "jobs-b")
        call_b = _make_call(conn, tenant_b, "jobs-b")
        job_b = conn.execute(
            text(
                "INSERT INTO ops.processing_jobs (tenant_id, call_id, job_type) "
                "VALUES (:tenant_id, :call_id, 'transcribe') RETURNING id"
            ),
            {"tenant_id": tenant_b, "call_id": call_b},
        ).scalar_one()

    # Conexion nueva, SIN llamar a tenant_session(): un worker deberia ver
    # los trabajos pendientes de ambos tenants para poder repartirlos.
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id FROM ops.processing_jobs WHERE id IN (:a, :b) ORDER BY id"),
            {"a": job_a, "b": job_b},
        ).scalars().all()

    assert set(rows) == {job_a, job_b}
