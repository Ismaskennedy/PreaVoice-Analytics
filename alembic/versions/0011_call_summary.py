"""resumen de la llamada y quien contesto, en app.call_analyses

Pedido explicito del usuario: en vez de mostrar la transcripcion completa
como lo principal en pantalla, mostrar un resumen (quien contesto -- titular,
familiar, tercero, no contesto -- y lo mas importante de la llamada). La
transcripcion completa se sigue guardando igual (es la evidencia detras de
cada cita del checklist) pero deja de ser lo primero que se ve.

who_answered sigue la regla #4 (afirma un hecho de la llamada, necesita
evidencia) igual que las emociones. summary es una sintesis de toda la
llamada, no una afirmacion puntual -- no lleva el mismo CHECK.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

WHO_ANSWERED_VALUES = ("titular", "familiar", "tercero", "no_contesto")


def upgrade() -> None:
    values_sql = ", ".join(f"'{v}'" for v in WHO_ANSWERED_VALUES)
    op.execute(f"""
        ALTER TABLE app.call_analyses
            ADD COLUMN who_answered text CHECK (who_answered IN ({values_sql})),
            ADD COLUMN who_answered_quote text,
            ADD COLUMN who_answered_start_ms integer,
            ADD COLUMN who_answered_end_ms integer,
            ADD COLUMN summary text;
    """)

    op.execute("""
        ALTER TABLE app.call_analyses ADD CONSTRAINT call_analyses_who_answered_evidence CHECK (
            who_answered IS NULL
            OR (who_answered_quote IS NOT NULL AND who_answered_start_ms IS NOT NULL AND who_answered_end_ms IS NOT NULL)
        );
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE app.call_analyses
            DROP COLUMN IF EXISTS who_answered,
            DROP COLUMN IF EXISTS who_answered_quote,
            DROP COLUMN IF EXISTS who_answered_start_ms,
            DROP COLUMN IF EXISTS who_answered_end_ms,
            DROP COLUMN IF EXISTS summary;
    """)
