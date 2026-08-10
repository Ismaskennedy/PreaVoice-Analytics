"""clientes, asignaciones, llamadas, grabaciones y transcripciones

Agrega el resto de la cadena "subir/descargar la grabacion -> transcribir":
app.clients (la cartera de cobranza), app.agent_assignments (a que cartera
esta asignado cada gestor ahora mismo), app.resolve_assignment() + un
trigger que congela calls.client_id al insertar (regla #2 de CLAUDE.md: si
el gestor cambia de cartera despues, las llamadas viejas no se mueven), y
las tablas de grabaciones y transcripciones.

El v1 soporta dos formas de cargar una llamada: arrastrar un archivo a mano,
o subir un CSV con URLs de descarga (el sistema descarga el .mp3). Por eso
app.recordings tiene source_url (nulo si fue carga manual) y app.calls tiene
occurred_at (la fecha/hora real de la llamada, que en la carga por CSV viene
en el archivo y en la manual puede no conocerse todavia).

La columna 'usuario' del CSV es un codigo interno del sistema de origen, no
un correo, asi que se agrega app.users.external_code para poder cruzarlo.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = (
    "app.clients",
    "app.agent_assignments",
    "app.calls",
    "app.recordings",
    "app.transcripts",
    "app.transcript_segments",
)


def upgrade() -> None:
    # Codigo del gestor en el sistema de origen (ej. Vicidial), para cruzar
    # la columna 'usuario' del CSV de carga por lotes. No todos los usuarios
    # necesitan uno (ej. Calidad no toma llamadas), por eso es opcional y el
    # indice unico de abajo solo aplica a las filas que si lo tienen.
    op.execute("ALTER TABLE app.users ADD COLUMN external_code text;")
    op.execute("""
        CREATE UNIQUE INDEX users_external_code_unique
            ON app.users (tenant_id, external_code)
            WHERE external_code IS NOT NULL;
    """)

    op.execute("""
        CREATE TABLE app.clients (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
            name        text NOT NULL,
            code        text NOT NULL,
            is_active   boolean NOT NULL DEFAULT true,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, code)
        );
    """)

    # Una fila por gestor: su cartera ACTUAL. Se actualiza en el lugar
    # cuando lo reasignan; el historial de a quien perteneceio cada llamada
    # vieja vive en calls.client_id, no aqui.
    op.execute("""
        CREATE TABLE app.agent_assignments (
            tenant_id    uuid NOT NULL REFERENCES app.tenants(id),
            user_id      uuid PRIMARY KEY REFERENCES app.users(id),
            client_id    uuid NOT NULL REFERENCES app.clients(id),
            assigned_at  timestamptz NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE FUNCTION app.resolve_assignment(p_user_id uuid) RETURNS uuid
        LANGUAGE sql STABLE
        AS $$
            SELECT client_id FROM app.agent_assignments WHERE user_id = p_user_id;
        $$;
    """)

    op.execute("""
        CREATE TABLE app.calls (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL REFERENCES app.tenants(id),
            client_id    uuid REFERENCES app.clients(id),
            agent_id     uuid NOT NULL REFERENCES app.users(id),
            uploaded_by  uuid NOT NULL REFERENCES app.users(id),
            uploaded_at  timestamptz NOT NULL DEFAULT now(),
            occurred_at  timestamptz,
            status       text NOT NULL DEFAULT 'uploaded'
                CHECK (status IN ('uploaded', 'transcribing', 'transcribed', 'failed'))
        );
    """)

    # Congela client_id al insertar, tomando la asignacion vigente del
    # gestor en ese momento (regla #2). Si ya viene client_id explicito
    # (por ejemplo en pruebas) se respeta tal cual.
    op.execute("""
        CREATE FUNCTION app.calls_freeze_client_id() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.client_id IS NULL THEN
                NEW.client_id := app.resolve_assignment(NEW.agent_id);
            END IF;
            IF NEW.client_id IS NULL THEN
                RAISE EXCEPTION
                    'No se pudo resolver el cliente/cartera para el agente %: no tiene asignacion activa en app.agent_assignments',
                    NEW.agent_id;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER calls_freeze_client_id
            BEFORE INSERT ON app.calls
            FOR EACH ROW
            EXECUTE FUNCTION app.calls_freeze_client_id();
    """)

    op.execute("""
        CREATE TABLE app.recordings (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL REFERENCES app.tenants(id),
            call_id            uuid NOT NULL REFERENCES app.calls(id),
            storage_path       text NOT NULL,
            source_url         text,
            original_filename  text NOT NULL,
            checksum_sha256    text NOT NULL,
            format             text NOT NULL,
            duration_ms        integer,
            uploaded_at        timestamptz NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE app.transcripts (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES app.tenants(id),
            call_id     uuid NOT NULL REFERENCES app.calls(id),
            provider    text NOT NULL,
            language    text,
            state       text NOT NULL DEFAULT 'CURRENT' CHECK (state IN ('CURRENT', 'SUPERSEDED')),
            created_at  timestamptz NOT NULL DEFAULT now()
        );
    """)

    # Indice parcial: un indice que solo cubre las filas que cumplen la
    # condicion (aqui, state = 'CURRENT'). Sirve para exigir "maximo una
    # transcripcion vigente por llamada" sin impedir que existan varias
    # SUPERSEDED del mismo call_id (regla #3: nada se borra en un reproceso).
    op.execute("""
        CREATE UNIQUE INDEX transcripts_one_current_per_call
            ON app.transcripts (call_id)
            WHERE state = 'CURRENT';
    """)

    op.execute("""
        CREATE TABLE app.transcript_segments (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES app.tenants(id),
            transcript_id uuid NOT NULL REFERENCES app.transcripts(id),
            speaker       text,
            start_ms      integer NOT NULL,
            end_ms        integer NOT NULL,
            text          text NOT NULL,
            CHECK (end_ms >= start_ms)
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
    op.execute("DROP TABLE IF EXISTS app.transcript_segments;")
    op.execute("DROP TABLE IF EXISTS app.transcripts;")
    op.execute("DROP TABLE IF EXISTS app.recordings;")
    op.execute("DROP TRIGGER IF EXISTS calls_freeze_client_id ON app.calls;")
    op.execute("DROP FUNCTION IF EXISTS app.calls_freeze_client_id();")
    op.execute("DROP TABLE IF EXISTS app.calls;")
    op.execute("DROP FUNCTION IF EXISTS app.resolve_assignment(uuid);")
    op.execute("DROP TABLE IF EXISTS app.agent_assignments;")
    op.execute("DROP TABLE IF EXISTS app.clients;")
    op.execute("DROP INDEX IF EXISTS app.users_external_code_unique;")
    op.execute("ALTER TABLE app.users DROP COLUMN IF EXISTS external_code;")
