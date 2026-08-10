"""emociones (asesor/deudor) y calidad de audio en app.call_analyses

Pedido explicito del usuario despues de probar con una llamada real: falta
saber que emocion tenia el asesor y el deudor, y si la llamada tuvo mala
calidad de audio.

Las emociones siguen la regla #4 igual que los hallazgos del checklist: si
hay una etiqueta, tiene que venir con cita y marcas de tiempo, o se anula.
La calidad de audio es distinta -- no es una "afirmacion" de la IA sino una
medicion derivada de que tan segura estuvo Whisper de cada fragmento que
transcribio (avg_logprob), asi que no lleva el mismo CHECK de evidencia.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

EMOTIONS = (
    "tranquilo", "empatico", "neutral", "impaciente",
    "frustrado", "molesto", "agresivo", "ansioso", "cooperativo",
)


def upgrade() -> None:
    emotions_sql = ", ".join(f"'{e}'" for e in EMOTIONS)

    op.execute(f"""
        ALTER TABLE app.call_analyses
            ADD COLUMN agent_emotion text CHECK (agent_emotion IN ({emotions_sql})),
            ADD COLUMN agent_emotion_quote text,
            ADD COLUMN agent_emotion_start_ms integer,
            ADD COLUMN agent_emotion_end_ms integer,
            ADD COLUMN debtor_emotion text CHECK (debtor_emotion IN ({emotions_sql})),
            ADD COLUMN debtor_emotion_quote text,
            ADD COLUMN debtor_emotion_start_ms integer,
            ADD COLUMN debtor_emotion_end_ms integer,
            ADD COLUMN audio_quality text CHECK (audio_quality IN ('buena', 'regular', 'mala')),
            ADD COLUMN audio_quality_notes text;
    """)

    # Regla #4: sin cita completa, no puede haber etiqueta de emocion.
    op.execute("""
        ALTER TABLE app.call_analyses ADD CONSTRAINT call_analyses_agent_emotion_evidence CHECK (
            agent_emotion IS NULL
            OR (agent_emotion_quote IS NOT NULL AND agent_emotion_start_ms IS NOT NULL AND agent_emotion_end_ms IS NOT NULL)
        );
    """)
    op.execute("""
        ALTER TABLE app.call_analyses ADD CONSTRAINT call_analyses_debtor_emotion_evidence CHECK (
            debtor_emotion IS NULL
            OR (debtor_emotion_quote IS NOT NULL AND debtor_emotion_start_ms IS NOT NULL AND debtor_emotion_end_ms IS NOT NULL)
        );
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE app.call_analyses
            DROP COLUMN IF EXISTS agent_emotion,
            DROP COLUMN IF EXISTS agent_emotion_quote,
            DROP COLUMN IF EXISTS agent_emotion_start_ms,
            DROP COLUMN IF EXISTS agent_emotion_end_ms,
            DROP COLUMN IF EXISTS debtor_emotion,
            DROP COLUMN IF EXISTS debtor_emotion_quote,
            DROP COLUMN IF EXISTS debtor_emotion_start_ms,
            DROP COLUMN IF EXISTS debtor_emotion_end_ms,
            DROP COLUMN IF EXISTS audio_quality,
            DROP COLUMN IF EXISTS audio_quality_notes;
    """)
