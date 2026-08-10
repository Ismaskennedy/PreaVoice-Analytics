"""Valida taxonomias (datos, no codigo - regla #6) y el checklist configurable."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _make_tenant_and_client(conn, label):
    tenant_id = uuid.uuid4()
    client_id = uuid.uuid4()

    conn.execute(
        text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tenant_id, "name": f"Tenant {label}", "slug": f"tenant-{label}-{tenant_id}"},
    )
    conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
    conn.execute(
        text("INSERT INTO app.clients (id, tenant_id, name, code) VALUES (:id, :tenant_id, :name, :code)"),
        {"id": client_id, "tenant_id": tenant_id, "name": f"Cliente {label}", "code": f"cliente-{label}"},
    )
    return tenant_id, client_id


def _make_taxonomy_value(conn, tenant_id, label, is_legal_risk=False):
    category_id = uuid.uuid4()
    value_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO app.taxonomy_categories (id, tenant_id, code, name) "
            "VALUES (:id, :tenant_id, :code, :name)"
        ),
        {"id": category_id, "tenant_id": tenant_id, "code": f"categoria-{label}", "name": f"Categoria {label}"},
    )
    conn.execute(
        text(
            "INSERT INTO app.taxonomy_values (id, tenant_id, category_id, code, label, is_legal_risk) "
            "VALUES (:id, :tenant_id, :category_id, :code, :label, :is_legal_risk)"
        ),
        {
            "id": value_id,
            "tenant_id": tenant_id,
            "category_id": category_id,
            "code": f"valor-{label}",
            "label": f"Valor {label}",
            "is_legal_risk": is_legal_risk,
        },
    )
    return category_id, value_id


def test_taxonomy_value_legal_risk_flag(engine):
    with engine.begin() as conn:
        tenant_id, _client_id = _make_tenant_and_client(conn, "riesgo")
        _category_id, value_id = _make_taxonomy_value(conn, tenant_id, "amenaza", is_legal_risk=True)

        is_legal_risk = conn.execute(
            text("SELECT is_legal_risk FROM app.taxonomy_values WHERE id = :id"), {"id": value_id}
        ).scalar_one()

    assert is_legal_risk is True


def test_checklist_item_requires_existing_taxonomy_value(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            tenant_id, client_id = _make_tenant_and_client(conn, "fk-checklist")
            checklist_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO app.checklists (id, tenant_id, client_id, name) "
                    "VALUES (:id, :tenant_id, :client_id, :name)"
                ),
                {"id": checklist_id, "tenant_id": tenant_id, "client_id": client_id, "name": "Checklist fk"},
            )
            conn.execute(
                text(
                    "INSERT INTO app.checklist_items (tenant_id, checklist_id, taxonomy_value_id) "
                    "VALUES (:tenant_id, :checklist_id, :taxonomy_value_id)"
                ),
                {"tenant_id": tenant_id, "checklist_id": checklist_id, "taxonomy_value_id": uuid.uuid4()},
            )


def test_only_one_active_checklist_per_client(engine):
    with engine.begin() as conn:
        tenant_id, client_id = _make_tenant_and_client(conn, "activo-unico")
        conn.execute(
            text("INSERT INTO app.checklists (tenant_id, client_id, name) VALUES (:tenant_id, :client_id, :name)"),
            {"tenant_id": tenant_id, "client_id": client_id, "name": "Checklist v1"},
        )

    # Un segundo checklist activo para el mismo cliente debe rechazarse.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
            conn.execute(
                text(
                    "INSERT INTO app.checklists (tenant_id, client_id, name) "
                    "VALUES (:tenant_id, :client_id, :name)"
                ),
                {"tenant_id": tenant_id, "client_id": client_id, "name": "Checklist v2"},
            )

    # Si desactivo el primero, si se puede crear uno nuevo activo.
    with engine.begin() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
        conn.execute(
            text("UPDATE app.checklists SET is_active = false WHERE client_id = :client_id"),
            {"client_id": client_id},
        )
        conn.execute(
            text(
                "INSERT INTO app.checklists (tenant_id, client_id, name) "
                "VALUES (:tenant_id, :client_id, :name)"
            ),
            {"tenant_id": tenant_id, "client_id": client_id, "name": "Checklist v2"},
        )
        active_count = conn.execute(
            text("SELECT count(*) FROM app.checklists WHERE client_id = :client_id AND is_active = true"),
            {"client_id": client_id},
        ).scalar_one()

    assert active_count == 1


def test_taxonomy_values_respect_tenant_isolation(engine):
    with engine.begin() as conn:
        tenant_a, _client_a = _make_tenant_and_client(conn, "rls-a")
        _cat_a, value_a = _make_taxonomy_value(conn, tenant_a, "solo-a")

    with engine.begin() as conn:
        tenant_b, _client_b = _make_tenant_and_client(conn, "rls-b")
        _cat_b, value_b = _make_taxonomy_value(conn, tenant_b, "solo-b")

    query = text("SELECT id FROM app.taxonomy_values WHERE id IN (:a, :b)")

    with engine.connect() as conn:
        rows = conn.execute(query, {"a": value_a, "b": value_b}).scalars().all()
        assert rows == []

    with engine.connect() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_a})
        rows = conn.execute(query, {"a": value_a, "b": value_b}).scalars().all()
        assert rows == [value_a]
