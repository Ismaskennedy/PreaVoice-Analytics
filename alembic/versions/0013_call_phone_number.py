"""telefono del deudor en app.calls, para la carga por CSV

Pendiente anotado desde la Fase 2: el telefono viene escondido en el nombre
del archivo de la grabacion (o, en el CSV real que se uso para probar esto,
en su propia columna 'phone_number'). Se guarda en la llamada -- no en la
grabacion -- porque es un dato del deudor/llamada, no del archivo de audio
en si.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-12
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.calls ADD COLUMN phone_number text;")


def downgrade() -> None:
    op.execute("ALTER TABLE app.calls DROP COLUMN IF EXISTS phone_number;")
