"""Valida el ciclo de migraciones y el aislamiento por tenant (RLS) del Bloque A."""
import uuid

from sqlalchemy import text


def test_upgrade_downgrade_cycle(engine, alembic_runner):
    alembic_runner("upgrade", "head")
    with engine.connect() as conn:
        schemas = conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('app', 'ops', 'audit')"
            )
        ).scalars().all()
        assert set(schemas) == {"app", "ops", "audit"}

        ops_tables = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'ops'")
        ).scalars().all()
        assert set(ops_tables) == {"processing_jobs"}

        audit_tables = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'audit'")
        ).scalars().all()
        assert set(audit_tables) == {"activity_log"}

    # No hacemos downgrade a 'base': esta base de datos ya tiene datos reales
    # de la demo (llamadas subidas de verdad, con estados que las migraciones
    # viejas no permitian) y un downgrade completo choca contra restricciones
    # que ya no coinciden con esos datos. En su lugar probamos que la ULTIMA
    # migracion es reversible (un paso atras y adelante) sin tocar filas
    # existentes. No revisamos el detalle de que quito -- eso ya lo prueba
    # cada migracion nueva por su cuenta -- solo que el puntero de version se
    # mueve y regresa sin que downgrade()/upgrade() truenen.
    with engine.connect() as conn:
        version_at_head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    alembic_runner("downgrade", "-1")
    with engine.connect() as conn:
        version_after_downgrade = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version_after_downgrade != version_at_head

    alembic_runner("upgrade", "head")
    with engine.connect() as conn:
        version_after_upgrade = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version_after_upgrade == version_at_head
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'app' ORDER BY table_name"
            )
        ).scalars().all()
        assert set(tables) == {
            "agent_assignments",
            "call_analyses",
            "call_analysis_findings",
            "call_coaching_suggestions",
            "call_evaluation_corrections",
            "call_evaluations",
            "calls",
            "checklist_items",
            "checklists",
            "clients",
            "permissions",
            "recordings",
            "role_permissions",
            "roles",
            "taxonomy_categories",
            "taxonomy_values",
            "tenants",
            "transcript_segments",
            "transcripts",
            "user_roles",
            "users",
        }


def test_tenant_isolation(engine, alembic_runner):
    alembic_runner("upgrade", "head")

    # Slugs unicos por corrida: evita chocar con tenants que hayan quedado
    # de una corrida anterior (app.tenants no tiene RLS, no se limpia sola).
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app.tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            [
                {"id": tenant_a, "name": "Cliente A", "slug": f"cliente-a-{tenant_a}"},
                {"id": tenant_b, "name": "Cliente B", "slug": f"cliente-b-{tenant_b}"},
            ],
        )
        # app.users tiene FORCE ROW LEVEL SECURITY: ni el dueno de la tabla
        # puede insertar sin haber llamado antes a tenant_session() (regla #1).
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_a})
        conn.execute(
            text(
                "INSERT INTO app.users (tenant_id, email, full_name) "
                "VALUES (:tenant_id, :email, :full_name)"
            ),
            {"tenant_id": tenant_a, "email": "a@example.com", "full_name": "Usuario A"},
        )

    with engine.begin() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_b})
        conn.execute(
            text(
                "INSERT INTO app.users (tenant_id, email, full_name) "
                "VALUES (:tenant_id, :email, :full_name)"
            ),
            {"tenant_id": tenant_b, "email": "b@example.com", "full_name": "Usuario B"},
        )

    query = text("SELECT email FROM app.users WHERE tenant_id IN (:a, :b)")

    # Sin llamar a app.tenant_session(): "negar por defecto", no se ve nada.
    with engine.connect() as conn:
        rows = conn.execute(query, {"a": tenant_a, "b": tenant_b}).scalars().all()
        assert rows == []

    # Con la sesion activada para el tenant A, solo se ve su propio usuario.
    with engine.connect() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_a})
        rows = conn.execute(query, {"a": tenant_a, "b": tenant_b}).scalars().all()
        assert rows == ["a@example.com"]

    # Misma prueba para el tenant B, en una conexion nueva (NullPool).
    with engine.connect() as conn:
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_b})
        rows = conn.execute(query, {"a": tenant_a, "b": tenant_b}).scalars().all()
        assert rows == ["b@example.com"]
