"""analisis de IA con evidencia obligatoria

Agrega app.call_analyses (el resultado crudo del LLM para una llamada,
version con estado CURRENT/SUPERSEDED como los transcripts) y
app.call_analysis_findings (un renglon por item del checklist evaluado).

La regla #4 de CLAUDE.md ("toda afirmacion de la IA lleva evidencia, si no
tiene se anula") queda escrita como un CHECK en la base de datos, no solo
como convencion: is_met (si el item se cumplio o no) solo puede tener un
valor si hay una cita textual (evidence_quote) y sus marcas de tiempo. Sin
evidencia, is_met tiene que quedar en NULL.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = (
    "app.call_analyses",
    "app.call_analysis_findings",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.call_analyses (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES app.tenants(id),
            call_id         uuid NOT NULL REFERENCES app.calls(id),
            transcript_id   uuid NOT NULL REFERENCES app.transcripts(id),
            model           text NOT NULL,
            prompt_version  text NOT NULL,
            temperature     numeric NOT NULL DEFAULT 0,
            raw_response    jsonb NOT NULL,
            state           text NOT NULL DEFAULT 'CURRENT' CHECK (state IN ('CURRENT', 'SUPERSEDED')),
            created_at      timestamptz NOT NULL DEFAULT now()
        );
    """)

    # Mismo patron que transcripts_one_current_per_call (migracion 0002):
    # un indice parcial para exigir un solo analisis vigente por llamada,
    # sin impedir que existan versiones viejas SUPERSEDED (regla #3).
    op.execute("""
        CREATE UNIQUE INDEX call_analyses_one_current_per_call
            ON app.call_analyses (call_id)
            WHERE state = 'CURRENT';
    """)

    op.execute("""
        CREATE TABLE app.call_analysis_findings (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL REFERENCES app.tenants(id),
            analysis_id        uuid NOT NULL REFERENCES app.call_analyses(id),
            checklist_item_id  uuid NOT NULL REFERENCES app.checklist_items(id),
            is_met             boolean,
            evidence_start_ms  integer,
            evidence_end_ms    integer,
            evidence_quote     text,
            notes              text,
            created_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (analysis_id, checklist_item_id),
            CHECK (evidence_start_ms IS NULL OR evidence_end_ms IS NULL OR evidence_end_ms >= evidence_start_ms),
            -- Regla #4: si no hay cita textual con sus marcas de tiempo,
            -- is_met no puede tener un valor (se anula).
            CHECK (
                is_met IS NULL
                OR (evidence_quote IS NOT NULL AND evidence_start_ms IS NOT NULL AND evidence_end_ms IS NOT NULL)
            )
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
    op.execute("DROP TABLE IF EXISTS app.call_analysis_findings;")
    op.execute("DROP TABLE IF EXISTS app.call_analyses;")
