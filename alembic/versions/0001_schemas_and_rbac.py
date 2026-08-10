"""esquemas base y control de acceso (RBAC)

Crea los tres esquemas del proyecto (app, ops, audit), la funcion
app.tenant_session() que activa el aislamiento por cliente (regla #1 de
CLAUDE.md), y las tablas de control de acceso basado en roles: tenants,
users, roles, permissions, role_permissions, user_roles.

Escrito en SQL crudo a proposito (regla #5 de CLAUDE.md): RLS y las
restricciones que necesitamos no se generan bien con el DSL de Alembic.

Revision ID: 0001
Revises:
Create Date: 2026-08-06
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE SCHEMA IF NOT EXISTS app;
        CREATE SCHEMA IF NOT EXISTS ops;
        CREATE SCHEMA IF NOT EXISTS audit;
    """)

    # gen_random_uuid() vive en pgcrypto en versiones viejas de Postgres;
    # en Postgres 13+ ya esta en el nucleo, pero pedirla no hace dano.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.execute("""
        CREATE TABLE app.tenants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text NOT NULL,
            slug        text NOT NULL UNIQUE,
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE app.users (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
            email       text NOT NULL,
            full_name   text NOT NULL,
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, email)
        );
    """)

    # roles y permisos son catalogo global (no llevan tenant_id): el mismo
    # rol "Calidad" o "Agente" existe para cualquier cliente.
    op.execute("""
        CREATE TABLE app.roles (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code        text NOT NULL UNIQUE,
            name        text NOT NULL,
            description text
        );
    """)

    op.execute("""
        CREATE TABLE app.permissions (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code        text NOT NULL UNIQUE,
            description text
        );
    """)

    op.execute("""
        CREATE TABLE app.role_permissions (
            role_id       uuid NOT NULL REFERENCES app.roles(id),
            permission_id uuid NOT NULL REFERENCES app.permissions(id),
            PRIMARY KEY (role_id, permission_id)
        );
    """)

    # tenant_id se repite aqui a proposito: permite que la politica de RLS
    # filtre esta tabla directamente, sin depender de un JOIN contra users.
    op.execute("""
        CREATE TABLE app.user_roles (
            tenant_id  uuid NOT NULL REFERENCES app.tenants(id),
            user_id    uuid NOT NULL REFERENCES app.users(id),
            role_id    uuid NOT NULL REFERENCES app.roles(id),
            PRIMARY KEY (user_id, role_id)
        );
    """)

    # Activa el aislamiento entre clientes para la sesion de base de datos
    # actual. Toda consulta de negocio debe pasar por aqui (regla #1).
    op.execute("""
        CREATE FUNCTION app.tenant_session(p_tenant_id uuid) RETURNS void
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM set_config('app.current_tenant_id', p_tenant_id::text, false);
        END;
        $$;
    """)

    for table in ("app.users", "app.user_roles"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # current_setting(..., true) da NULL si tenant_session() nunca se
        # llamo en esta sesion -> tenant_id = NULL nunca es verdadero ->
        # sin sesion activa no se ve ninguna fila. Es "negar por defecto".
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.tenant_session(uuid);")
    op.execute("DROP TABLE IF EXISTS app.user_roles;")
    op.execute("DROP TABLE IF EXISTS app.role_permissions;")
    op.execute("DROP TABLE IF EXISTS app.permissions;")
    op.execute("DROP TABLE IF EXISTS app.roles;")
    op.execute("DROP TABLE IF EXISTS app.users;")
    op.execute("DROP TABLE IF EXISTS app.tenants;")
    op.execute("DROP SCHEMA IF EXISTS audit;")
    op.execute("DROP SCHEMA IF EXISTS ops;")
    op.execute("DROP SCHEMA IF EXISTS app;")
