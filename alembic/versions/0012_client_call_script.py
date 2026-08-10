"""guion esperado de la llamada, en app.clients

Pedido explicito del usuario: el checklist (app.checklist_items) solo permite
criterios puntuales de si/no ("no debe decir X"). Ademas de eso, Calidad
necesita poder describir en texto libre todo lo que una llamada de este
cliente debe cubrir para aprobarse -- un "guion esperado" mas amplio, no una
lista de prohibiciones. Este campo es contexto adicional para la IA, no
reemplaza el checklist puntual: los findings con evidencia (regla #4) siguen
saliendo solo del checklist.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.clients ADD COLUMN call_script text;")


def downgrade() -> None:
    op.execute("ALTER TABLE app.clients DROP COLUMN IF EXISTS call_script;")
