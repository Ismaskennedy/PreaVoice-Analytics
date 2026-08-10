"""amplia los estados posibles de app.calls.status

La Fase 2 dejo status limitado a ('uploaded','transcribing','transcribed',
'failed') a proposito, porque todavia no existia el paso de analisis. Ahora
que el flujo sincrono de subir->transcribir->analizar->calificar ya llama
al LLM, hacen falta 'analyzing' y 'analyzed'.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE app.calls DROP CONSTRAINT calls_status_check;")
    op.execute("""
        ALTER TABLE app.calls ADD CONSTRAINT calls_status_check
        CHECK (status IN ('uploaded', 'transcribing', 'transcribed', 'analyzing', 'analyzed', 'failed'));
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE app.calls DROP CONSTRAINT calls_status_check;")
    op.execute("""
        ALTER TABLE app.calls ADD CONSTRAINT calls_status_check
        CHECK (status IN ('uploaded', 'transcribing', 'transcribed', 'failed'));
    """)
