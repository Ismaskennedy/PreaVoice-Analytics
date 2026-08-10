"""Valida app.resolve_assignment() (regla #2: client_id se congela al insertar
la llamada) y que app.calls respeta el aislamiento por tenant (RLS)."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError


def _make_tenant_client_agent(conn, label):
    """Crea un tenant con un cliente/cartera y un gestor ya asignado a el.
    Devuelve (tenant_id, client_id, agent_id)."""
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    conn.execute(
        text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tenant_id, "name": f"Tenant {label}", "slug": f"tenant-{label}-{tenant_id}"},
    )
    conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
    conn.execute(
        text(
            "INSERT INTO app.users (id, tenant_id, email, full_name) "
            "VALUES (:id, :tenant_id, :email, :full_name)"
        ),
        {
            "id": agent_id,
            "tenant_id": tenant_id,
            "email": f"agente-{label}@example.com",
            "full_name": f"Agente {label}",
        },
    )
    conn.execute(
        text(
            "INSERT INTO app.clients (id, tenant_id, name, code) "
            "VALUES (:id, :tenant_id, :name, :code)"
        ),
        {"id": client_id, "tenant_id": tenant_id, "name": f"Cliente {label}", "code": f"cliente-{label}"},
    )
    conn.execute(
        text(
            "INSERT INTO app.agent_assignments (tenant_id, user_id, client_id) "
            "VALUES (:tenant_id, :user_id, :client_id)"
        ),
        {"tenant_id": tenant_id, "user_id": agent_id, "client_id": client_id},
    )
    return tenant_id, client_id, agent_id


def _insert_call(conn, tenant_id, agent_id, call_id=None):
    call_id = call_id or uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO app.calls (id, tenant_id, agent_id, uploaded_by) "
            "VALUES (:id, :tenant_id, :agent_id, :agent_id)"
        ),
        {"id": call_id, "tenant_id": tenant_id, "agent_id": agent_id},
    )
    return call_id


def test_resolve_assignment_freezes_client_id(engine):
    with engine.begin() as conn:
        tenant_id, client_id, agent_id = _make_tenant_client_agent(conn, "freeze")
        call_id = _insert_call(conn, tenant_id, agent_id)

        frozen_client_id = conn.execute(
            text("SELECT client_id FROM app.calls WHERE id = :id"), {"id": call_id}
        ).scalar_one()

    assert frozen_client_id == client_id


def test_call_without_assignment_raises(engine):
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    # Todo dentro de una sola transaccion: si el INSERT en app.calls falla
    # (como debe), la transaccion completa se revierte al salir del bloque.
    with pytest.raises(ProgrammingError, match="no tiene asignacion activa"):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": tenant_id, "name": "Tenant sin asignacion", "slug": f"tenant-sin-asignar-{tenant_id}"},
            )
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(
                text(
                    "INSERT INTO app.users (id, tenant_id, email, full_name) "
                    "VALUES (:id, :tenant_id, :email, :full_name)"
                ),
                {"id": agent_id, "tenant_id": tenant_id, "email": "sin-asignar@example.com", "full_name": "Sin Asignar"},
            )
            _insert_call(conn, tenant_id, agent_id)


def test_old_calls_keep_original_client_when_agent_reassigned(engine):
    with engine.begin() as conn:
        tenant_id, client_a, agent_id = _make_tenant_client_agent(conn, "reasignado")

        old_call_id = _insert_call(conn, tenant_id, agent_id)

        # El gestor cambia de cartera.
        client_b = uuid.uuid4()
        conn.execute(
            text("INSERT INTO app.clients (id, tenant_id, name, code) VALUES (:id, :tenant_id, :name, :code)"),
            {"id": client_b, "tenant_id": tenant_id, "name": "Cliente B", "code": "cliente-b-reasignado"},
        )
        conn.execute(
            text("UPDATE app.agent_assignments SET client_id = :client_id WHERE user_id = :user_id"),
            {"client_id": client_b, "user_id": agent_id},
        )

        new_call_id = _insert_call(conn, tenant_id, agent_id)

        rows = conn.execute(
            text("SELECT id, client_id FROM app.calls WHERE id IN (:old, :new)"),
            {"old": old_call_id, "new": new_call_id},
        ).mappings().all()

    by_id = {row["id"]: row["client_id"] for row in rows}
    # La llamada vieja se queda con la cartera vieja; solo la nueva usa la nueva.
    assert by_id[old_call_id] == client_a
    assert by_id[new_call_id] == client_b


def test_calls_respect_tenant_isolation(engine):
    with engine.begin() as conn:
        tenant_a, _client_a, agent_a = _make_tenant_client_agent(conn, "rls-a")
        call_a = _insert_call(conn, tenant_a, agent_a)

    with engine.begin() as conn:
        tenant_b, _client_b, agent_b = _make_tenant_client_agent(conn, "rls-b")
        call_b = _insert_call(conn, tenant_b, agent_b)

    query = text("SELECT id FROM app.calls WHERE id IN (:a, :b)")

    with engine.connect() as conn:
        rows = conn.execute(query, {"a": call_a, "b": call_b}).scalars().all()
        assert rows == []

    with engine.connect() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_a})
        rows = conn.execute(query, {"a": call_a, "b": call_b}).scalars().all()
        assert rows == [call_a]
