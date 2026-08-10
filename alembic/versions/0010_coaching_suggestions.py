"""areas de oportunidad para el asesor: sugerencias de coaching

Pedido explicito del usuario: para cada llamada, que la IA identifique que
puede mejorar el asesor en la llamada (calidad general) y que puede decir
para cerrar la negociacion (obtener un compromiso de pago).

A diferencia de los hallazgos del checklist (regla #4: afirman un hecho,
necesitan evidencia obligatoria), esto son recomendaciones/consejos, no
afirmaciones sobre lo que paso en la llamada -- por eso NO llevan el mismo
CHECK de "evidencia obligatoria". Si el modelo si da una cita de contexto,
tiene que venir con sus marcas de tiempo (consistencia), pero puede dar la
sugerencia sin citar nada.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.call_coaching_suggestions (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL REFERENCES app.tenants(id),
            analysis_id         uuid NOT NULL REFERENCES app.call_analyses(id),
            category            text NOT NULL CHECK (category IN ('mejora_llamada', 'cierre_negociacion')),
            suggestion          text NOT NULL,
            reference_quote     text,
            reference_start_ms  integer,
            reference_end_ms    integer,
            created_at          timestamptz NOT NULL DEFAULT now(),
            CHECK (reference_quote IS NULL OR (reference_start_ms IS NOT NULL AND reference_end_ms IS NOT NULL))
        );
    """)

    op.execute("ALTER TABLE app.call_coaching_suggestions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE app.call_coaching_suggestions FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON app.call_coaching_suggestions
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.call_coaching_suggestions;")
