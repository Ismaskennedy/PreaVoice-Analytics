"""evaluaciones humanas y correcciones (regla #7: la IA propone, el humano
dispone)

Agrega app.call_evaluations (la calificacion de una llamada: borrador hasta
que Calidad la confirma) y app.call_evaluation_corrections (los cambios que
un humano le hace a un hallazgo de la IA, append-only: nunca se edita ni se
borra un renglon, solo se agregan).

Dos reglas quedan escritas como restricciones de la base de datos, no solo
como convencion:
- Una evaluacion 'confirmed' necesita quien y cuando la confirmo; una
  'draft' no puede tener esos datos todavia (CHECK).
- Una fila de app.call_evaluation_corrections no se puede editar ni borrar
  una vez escrita (trigger que rechaza UPDATE/DELETE).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = (
    "app.call_evaluations",
    "app.call_evaluation_corrections",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.call_evaluations (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES app.tenants(id),
            call_id       uuid NOT NULL REFERENCES app.calls(id),
            analysis_id   uuid NOT NULL REFERENCES app.call_analyses(id),
            state         text NOT NULL DEFAULT 'CURRENT' CHECK (state IN ('CURRENT', 'SUPERSEDED')),
            status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'confirmed')),
            score         numeric,
            confirmed_by  uuid REFERENCES app.users(id),
            confirmed_at  timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            -- Regla #7: una confirmada necesita quien y cuando; una en
            -- borrador todavia no tiene ninguno de los dos.
            CHECK (
                (status = 'confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)
                OR (status = 'draft' AND confirmed_by IS NULL AND confirmed_at IS NULL)
            )
        );
    """)

    # Mismo patron de indice parcial que transcripts y call_analyses: una
    # sola evaluacion vigente por llamada (regla #3: un reproceso no borra
    # la anterior, la marca SUPERSEDED).
    op.execute("""
        CREATE UNIQUE INDEX call_evaluations_one_current_per_call
            ON app.call_evaluations (call_id)
            WHERE state = 'CURRENT';
    """)

    op.execute("""
        CREATE TABLE app.call_evaluation_corrections (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL REFERENCES app.tenants(id),
            evaluation_id      uuid NOT NULL REFERENCES app.call_evaluations(id),
            finding_id         uuid REFERENCES app.call_analysis_findings(id),
            previous_is_met    boolean,
            corrected_is_met   boolean,
            corrected_score    numeric,
            reason             text NOT NULL,
            corrected_by       uuid NOT NULL REFERENCES app.users(id),
            corrected_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (corrected_is_met IS NOT NULL OR corrected_score IS NOT NULL)
        );
    """)

    # Append-only de verdad: un trigger, no solo la promesa de no tocarlo.
    op.execute("""
        CREATE FUNCTION app.forbid_mutation() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'app.call_evaluation_corrections es append-only: no se permite % en esta tabla', TG_OP;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER call_evaluation_corrections_append_only
            BEFORE UPDATE OR DELETE ON app.call_evaluation_corrections
            FOR EACH ROW
            EXECUTE FUNCTION app.forbid_mutation();
    """)

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS call_evaluation_corrections_append_only ON app.call_evaluation_corrections;")
    op.execute("DROP FUNCTION IF EXISTS app.forbid_mutation();")
    op.execute("DROP TABLE IF EXISTS app.call_evaluation_corrections;")
    op.execute("DROP TABLE IF EXISTS app.call_evaluations;")
