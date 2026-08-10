"""taxonomias y checklist configurable

Agrega app.taxonomy_categories + app.taxonomy_values (regla #6: las
taxonomias son datos, no codigo; agregar un valor nuevo es insertar una
fila) y app.checklists + app.checklist_items, que arman un checklist
seleccionando valores de taxonomia.

is_legal_risk en taxonomy_values marca las banderas de riesgo legal del
dominio de cobranza (amenazas, revelar la deuda a terceros, lenguaje
ofensivo, insistir tras solicitud de no contacto).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = (
    "app.taxonomy_categories",
    "app.taxonomy_values",
    "app.checklists",
    "app.checklist_items",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.taxonomy_categories (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
            code        text NOT NULL,
            name        text NOT NULL,
            description text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, code)
        );
    """)

    op.execute("""
        CREATE TABLE app.taxonomy_values (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES app.tenants(id),
            category_id   uuid NOT NULL REFERENCES app.taxonomy_categories(id),
            code          text NOT NULL,
            label         text NOT NULL,
            description   text,
            is_legal_risk boolean NOT NULL DEFAULT false,
            is_active     boolean NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, category_id, code)
        );
    """)

    # "Un checklist, un cliente" en v1, pero la tabla ya soporta varios: la
    # cartera (client_id) es la que decide que checklist le toca a una llamada.
    op.execute("""
        CREATE TABLE app.checklists (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
            client_id   uuid NOT NULL REFERENCES app.clients(id),
            name        text NOT NULL,
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
    """)

    # Indice parcial (como en la migracion 0002): solo cubre las filas con
    # is_active = true, para exigir "un solo checklist activo por cliente"
    # sin bloquear que existan checklists viejos desactivados.
    op.execute("""
        CREATE UNIQUE INDEX checklists_one_active_per_client
            ON app.checklists (client_id)
            WHERE is_active = true;
    """)

    op.execute("""
        CREATE TABLE app.checklist_items (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         uuid NOT NULL REFERENCES app.tenants(id),
            checklist_id      uuid NOT NULL REFERENCES app.checklists(id),
            taxonomy_value_id uuid NOT NULL REFERENCES app.taxonomy_values(id),
            is_required       boolean NOT NULL DEFAULT true,
            weight            integer NOT NULL DEFAULT 1,
            display_order     integer NOT NULL DEFAULT 0,
            UNIQUE (checklist_id, taxonomy_value_id)
        );
    """)

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.checklist_items;")
    op.execute("DROP TABLE IF EXISTS app.checklists;")
    op.execute("DROP TABLE IF EXISTS app.taxonomy_values;")
    op.execute("DROP TABLE IF EXISTS app.taxonomy_categories;")
