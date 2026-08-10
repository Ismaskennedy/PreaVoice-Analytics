"""bitacora de auditoria y cola de trabajos en segundo plano

Agrega audit.activity_log (quien hizo que y cuando; append-only, reusa el
mismo trigger app.forbid_mutation() de la migracion 0005) y
ops.processing_jobs (cola simple basada en tabla para transcribir/analizar,
consumida por *polling* - sin Redis ni RabbitMQ).

ops.processing_jobs es la PRIMERA tabla del proyecto sin RLS. Es una
excepcion a proposito: el proceso que reparte trabajos pendientes necesita
ver los de todos los clientes a la vez para decidir cual sigue, no solo los
de un tenant. El trabajo en si no expone contenido de la llamada (solo
referencia el call_id y su estado); cuando ese proceso vaya a tocar los
datos reales de la llamada (transcripcion, analisis), ahi si tiene que
llamar a app.tenant_session() como cualquier otro codigo.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit.activity_log (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES app.tenants(id),
            actor_user_id  uuid REFERENCES app.users(id),
            action         text NOT NULL,
            entity_type    text NOT NULL,
            entity_id      uuid NOT NULL,
            metadata       jsonb,
            created_at     timestamptz NOT NULL DEFAULT now()
        );
    """)

    op.execute("ALTER TABLE audit.activity_log ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit.activity_log FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON audit.activity_log
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    """)

    # Igual que las correcciones humanas (migracion 0005): una bitacora que
    # se puede editar no sirve como bitacora. Reusa la misma funcion.
    op.execute("""
        CREATE TRIGGER activity_log_append_only
            BEFORE UPDATE OR DELETE ON audit.activity_log
            FOR EACH ROW
            EXECUTE FUNCTION app.forbid_mutation();
    """)

    op.execute("""
        CREATE TABLE ops.processing_jobs (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES app.tenants(id),
            call_id        uuid NOT NULL REFERENCES app.calls(id),
            job_type       text NOT NULL CHECK (job_type IN ('transcribe', 'analyze')),
            status         text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'done', 'failed')),
            attempts       integer NOT NULL DEFAULT 0,
            last_error     text,
            available_at   timestamptz NOT NULL DEFAULT now(),
            claimed_at     timestamptz,
            completed_at   timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now()
        );
    """)

    # Sin RLS a proposito (ver docstring). Este indice es el que usaria un
    # worker para encontrar rapido "el siguiente trabajo pendiente".
    op.execute("""
        CREATE INDEX processing_jobs_pending_idx
            ON ops.processing_jobs (available_at)
            WHERE status = 'pending';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.processing_jobs;")
    op.execute("DROP TRIGGER IF EXISTS activity_log_append_only ON audit.activity_log;")
    op.execute("DROP TABLE IF EXISTS audit.activity_log;")
